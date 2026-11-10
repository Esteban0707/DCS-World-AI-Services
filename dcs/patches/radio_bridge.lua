-- DCS_AI_Service native command bridge: DCS's own radio menu commands to an airfield's ATC, sent the way the comms
-- menu sends them (data.pComm:sendRawMessage(command, {recipient communicator})), with no keystrokes. The approach is
-- VAICOM PRO's (MIT: code inside this file, a localhost UDP socket polled from the GUI update callback).
-- Why: DCS's own ATC holds AI arrivals while it thinks the player departs; aborting the player's departure in DCS
-- releases them (DCS-ATC, live 2026-10-26). At night an Inbound call turns DCS's runway lights on.
-- Safety: binds 127.0.0.1 only; requests are "<id>\t<unix time>\t<command>\t<target>" lines (an airfield name for
-- the ATC commands, a ground group's name for the troop commands, a wingman's number or "flight" for the wingman
-- orders); only the commands in COMMANDS, DESCENT and WINGMAN; never
-- runs received Lua; a request older than MAX_AGE_S is refused; one per REPEAT_S per
-- command and airfield. Every request is answered on the cockpit port: {type:"native", id, status, detail}.
do
	local PORT, REPLY_PORT = 47823, 47820
	local MAX_AGE_S, REPEAT_S, BIND_RETRY_S = 10, 10, 5
	local WINGMAN_REPEAT_S = 2   -- the same order to the same wingman (the pilot may repeat it on purpose)
	local COMMANDS = {
		abort_takeoff = 'wMsgLeaderAbortTakeoff',
		request_taxi = 'wMsgLeaderRequestTaxiToRunway',
		request_takeoff = 'wMsgLeaderRequestTakeOff',
		inbound = 'wMsgLeaderInbound',
		abort_inbound = 'wMsgLeaderAbortInbound',
		request_landing = 'wMsgLeaderRequestLanding',
	}
	-- Airborne troops and the player's helicopter, the way the menu's "Descent" items do it (this file's
	-- makeItemEmbarkDescent / makeItemDisembarkDescent): the group must be waiting with its EmbarkToTransport task
	-- (dcs/instructor_tasks.lua wait_pickup). The target is the group's name; when it is not found, the names DCS
	-- offers are reported.
	local function descent_names(list)
		local names = {}
		for _, descent in base.ipairs(list or {}) do names[#names + 1] = base.tostring(descent:getName()) end
		return base.table.concat(names, ', ')
	end
	local DESCENT = {
		embark_troops = function(target)
			local helicopter = data.pUnit
			local list = base.coalition.getAllDescents(helicopter:getObjectID(), true) or {}
			for _, descent in base.ipairs(list) do
				if descent:getName() == target then
					descent:embarking(helicopter:getObjectID())
					return 'sent', 'embarking ' .. target
				end
			end
			return 'unavailable', target .. ' is not ready to embark on this helicopter (offered: ' ..
			       descent_names(list) .. ')'
		end,
		disembark_troops = function(target)
			local helicopter = data.pUnit
			local list = base.coalition.getDescentsOnBoard(helicopter:getObjectID()) or {}
			for _, descent in base.ipairs(list) do
				if descent:getName() == target then
					helicopter:disembarking(descent:getID())
					return 'sent', 'disembarking ' .. target
				end
			end
			return 'unavailable', target .. ' is not on board (on board: ' .. descent_names(list) .. ')'
		end,
		troops_on_board = function()
			return 'sent', descent_names(base.coalition.getDescentsOnBoard(data.pUnit:getObjectID()))
		end,
	}

	-- Orders to the player's wingmen (2026-11-03), the "Flight" / "Wingman N" items of the F1 menu
	-- (Config/LockOnAirplane.lua, read 2026-11-03): the message, then the menu's own parameters after the recipient.
	-- The target is the wingman's number in the player's group ("2", "3", "4"), or "flight" (all of them): the menu
	-- passes number - 1, or 5 for the flight, as the first message parameter. Formation orders go to the flight, as
	-- in the menu. Like the menu, the wingman's communicator is selected first (selectAndTuneCommunicator: the
	-- player's radio that reaches it; DCS retunes only with easy comms): live 2026-11-03, without it the orders only
	-- showed on screen. DCS's wingmen hear orders only on the flight's Mission Editor frequency (wingman/desk.py
	-- refuses the orders when the agent is on another one).
	local WINGMAN = {
		wing_engage_my_target = {'wMsgLeaderEngageMyTarget', false},
		wing_engage_my_enemy = {'wMsgLeaderMyEnemyAttack', false},
		wing_engage_bandits = {'wMsgLeaderEngageBandits', false},
		wing_engage_air_defences = {'wMsgLeaderEngageAirDefenses', false},
		wing_engage_ground_targets = {'wMsgLeaderEngageGroundTargets', false},
		wing_engage_ships = {'wMsgLeaderEngageNavalTargets', false, false},
		wing_primary_rejoin = {'wMsgLeaderFulfilTheTaskAndJoinUp', false},
		wing_primary_rtb = {'wMsgLeaderFulfilTheTaskAndReturnToBase', false},
		wing_pincer_high = {'wMsgLeaderPincerHigh'},
		wing_pincer_low = {'wMsgLeaderPincerLow'},
		wing_pincer_right = {'wMsgLeaderPincerRight'},
		wing_pincer_left = {'wMsgLeaderPincerLeft'},
		wing_return_to_base = {'wMsgLeaderReturnToBase'},
		wing_fly_route = {'wMsgLeaderFlyRoute'},
		wing_hold_position = {'wMsgLeaderAnchorHere'},
		wing_go_tanker = {'wMsgLeaderGoRefueling'},
		wing_radar_on = {'wMsgLeaderRadarOn'},
		wing_radar_off = {'wMsgLeaderRadarOff'},
		wing_ecm_on = {'wMsgLeaderDisturberOn'},
		wing_ecm_off = {'wMsgLeaderDisturberOff'},
		wing_smoke_on = {'wMsgLeaderSmokeOn'},
		wing_smoke_off = {'wMsgLeaderSmokeOff'},
		wing_cover_me = {'wMsgLeaderCoverMe'},
		wing_jettison = {'wMsgLeaderJettisonWeapons'},
		wing_rejoin = {'wMsgLeaderJoinUp'},
		wing_line_abreast = {'wMsgLeaderLineAbreast', flight = true},
		wing_trail = {'wMsgLeaderGoTrail', flight = true},
		wing_wedge = {'wMsgLeaderWedge', flight = true},
		wing_echelon_right = {'wMsgLeaderEchelonRight', flight = true},
		wing_finger_four = {'wMsgLeaderFingerFour', flight = true},
		wing_spread_four = {'wMsgLeaderSpreadFour', flight = true},
		wing_close_formation = {'wMsgLeaderCloseFormation', flight = true},
		wing_open_formation = {'wMsgLeaderOpenFormation', flight = true},
		wing_close_group_formation = {'wMsgLeaderCloseGroupFormation', flight = true},
	}
	local FLIGHT = 5

	local function wingman_order(order, target)
		local group = data.pUnit:getGroup()
		local number = base.tonumber(target)
		if order.flight or target == 'flight' then
			local any = false
			for i = 2, 4 do
				if group:getUnit(i) then any = true end
			end
			if not any then return 'unavailable', 'no wingmen in the flight' end
			for i = 2, 4 do
				local wingman = group:getUnit(i)
				if wingman then base.pcall(function() selectAndTuneCommunicator(wingman:getCommunicator()) end); break end
			end
			local parameters = {FLIGHT}
			for i = 2, #order do parameters[#parameters + 1] = order[i] end
			data.pComm:sendRawMessage(base.Message[order[1]], parameters)
			return 'sent', order[1] .. ' to the flight'
		end
		if not number or number < 2 or number > 4 then return 'refused', 'bad wingman ' .. base.tostring(target) end
		local wingman = group:getUnit(number)
		if not wingman then return 'unavailable', 'no wingman ' .. number .. ' in the flight' end
		base.pcall(function() selectAndTuneCommunicator(wingman:getCommunicator()) end)
		local parameters = {number - 1}
		for i = 2, #order do parameters[#parameters + 1] = order[i] end
		data.pComm:sendRawMessage(base.Message[order[1]], parameters)
		return 'sent', order[1] .. ' to wingman ' .. number
	end

	local state = base.DCSAIServiceBridge or {last = {}}
	base.DCSAIServiceBridge = state

	local function log(text)
		if base.log and base.log.write then base.log.write('DCS_AI_Service', base.log.INFO, text) end
	end

	local function quote(value)
		return '"' .. base.string.gsub(base.tostring(value), '[%c"\\]', function(c)
			return base.string.format('\\u%04x', base.string.byte(c))
		end) .. '"'
	end

	local function reply(id, status, detail)
		if not state.udp then return end
		state.udp:sendto('{"type":"native","id":' .. quote(id) .. ',"status":' .. quote(status) .. ',"detail":' ..
		                 quote(detail or '') .. '}', '127.0.0.1', REPLY_PORT)
	end

	local function airfield_communicator(name)
		local unit = data.pUnit
		if not unit then return nil, 'no player unit' end
		for _, side in base.ipairs({base.coalition.side.NEUTRAL, unit:getCoalition()}) do
			for _, airbase in base.pairs(base.coalition.getServiceProviders(side, base.coalition.service.ATC) or {}) do
				if airbase:getName() == name then
					local communicator = airbase:getCommunicator()
					if communicator then return communicator end
					return nil, 'the airfield has no radio'
				end
			end
		end
		return nil, 'no ATC called ' .. name .. ' for this coalition'
	end

	local function dispatch(line)
		local id, created, command, airfield = base.string.match(line, '^(%w+)\t(%d+)\t([%w_]+)\t([^\r\n]+)$')
		if not id then return '?', 'refused', 'malformed request' end
		local now = base.os.time()
		if base.math.abs(now - base.tonumber(created)) > MAX_AGE_S then return id, 'refused', 'request too old' end
		local descent = DESCENT[command]
		local order = WINGMAN[command]
		local name = COMMANDS[command]
		local message = name and base.Message and base.Message[name]
		if order and not (base.Message and base.Message[order[1]]) then order = nil end
		if not message and not descent and not order then return id, 'refused', 'command not allowed: ' .. command end
		if not data.initialized or not data.pComm or not data.pUnit then return id, 'unavailable', 'radio menu not ready' end
		local key = command .. '|' .. airfield
		if command ~= 'troops_on_board' and state.last[key] and
				now - state.last[key] < (order and WINGMAN_REPEAT_S or REPEAT_S) then
			return id, 'refused', 'sent moments ago'
		end
		if order then
			state.last[key] = now
			state.wingmen_ordered = true   -- dcs/patches/speech_mute.lua silences DCS's own wingman voices from now on
			local status, detail = wingman_order(order, airfield)
			log(command .. ' ' .. airfield .. ': ' .. status .. ' ' .. detail)
			return id, status, detail
		end
		if descent then
			state.last[key] = now
			local status, detail = descent(airfield)
			if command ~= 'troops_on_board' then   -- asked every few seconds: not logged
				log(command .. ' ' .. airfield .. ': ' .. status .. ' ' .. detail)
			end
			return id, status, detail
		end
		local communicator, why = airfield_communicator(airfield)
		if not communicator then return id, 'unavailable', why end
		state.last[key] = now
		selectAndTuneCommunicator(communicator)   -- as the menu does before a command
		data.pComm:sendRawMessage(message, {communicator})
		log('sent ' .. command .. ' to ' .. airfield)
		return id, 'sent', command .. ' to ' .. airfield
	end

	local function bind()
		local now = base.os.time()
		if state.udp or (state.bind_at and now - state.bind_at < BIND_RETRY_S) then return end
		state.bind_at = now
		local ok, socket = base.pcall(base.require, 'socket')
		if not ok then log('bridge: no LuaSocket (' .. base.tostring(socket) .. ')'); return end
		local udp = socket.udp()
		udp:settimeout(0)
		local bound, err = udp:setsockname('127.0.0.1', PORT)
		if not bound then udp:close(); log('bridge: port ' .. PORT .. ' busy (' .. base.tostring(err) .. '), retrying'); return end
		state.udp = udp
		log('bridge listening on 127.0.0.1:' .. PORT)
	end

	-- Replaced on every load of this file (a mission reloads it): the closures see the current panel's data.
	state.tick = function()
		bind()
		if not state.udp then return end
		for _ = 1, 4 do
			local line = state.udp:receive()
			if not line then break end
			local ok, id, status, detail = base.pcall(dispatch, line)
			if not ok then id, status, detail = '?', 'error', base.tostring(id) end
			reply(id, status, detail)
		end
	end

	-- Live 2026-11-02: dxgui's update callback never fired in the mission ("bridge loaded", never "listening"), so
	-- our GUI hook (dcs/instructor_hook.lua, same "Main" Lua state) calls the tick every 0.1 s; it finds it here or
	-- in the panel's module table. The callback stays as a second driver; the tick is safe to call from both.
	local panel = base.package and base.package.loaded and base.package.loaded['RadioCommandDialogsPanel']
	if base.type(panel) == 'table' then panel.DCSAIServiceBridge = state end

	-- Registered the way VAICOM does it (Append.Core.RadioCommandDialogsPanel.lua, read 2026-11-02): at the end of the
	-- panel's initialize() (when DCS builds the radio menu for the player's unit), SetupApplicationUpdateCallback,
	-- EnableHighSpeedUpdate(true), then AddUpdateCallback. Registering once at file load (first try) never ran.
	local function start()
		local ok, Gui = base.pcall(base.require, 'dxgui')
		if not (ok and Gui and Gui.AddUpdateCallback) then
			log('bridge: no GUI update callback (' .. base.tostring(Gui) .. ')')
			return
		end
		if state.callback and Gui.RemoveUpdateCallback then base.pcall(Gui.RemoveUpdateCallback, state.callback) end
		state.callback = function()
			local bridge = base.DCSAIServiceBridge
			if bridge and bridge.tick then base.pcall(bridge.tick) end
		end
		if Gui.SetupApplicationUpdateCallback then Gui.SetupApplicationUpdateCallback() end
		if Gui.EnableHighSpeedUpdate then Gui.EnableHighSpeedUpdate(true) end
		Gui.AddUpdateCallback(state.callback)
		log('bridge started')
	end

	local panel_initialize = initialize
	if base.type(panel_initialize) == 'function' then
		initialize = function(...)   -- the panel's own initialize first, untouched
			panel_initialize(...)
			local ok, err = base.pcall(start)
			if not ok then log('bridge start failed: ' .. base.tostring(err)) end
		end
		log('bridge loaded')
	else
		log('bridge: the radio panel has no initialize()')
	end
end
