-- DCS-Instructor GUI hook. Loaded by Saved Games/DCS/Scripts/Hooks/DCSInstructorHook.lua (tools/install_hook.py),
-- which passes the project folder as this chunk's argument: DCS runs each hook file in its own environment, so a
-- global set there is not seen here (live 2026-11-01). Runs in DCS's GUI (hooks) environment, which, unlike the export
-- environment, can read the mission. Once per second it sends to the instructor (UDP JSON to the cockpit port):
-- - {type = "callsign", callsign, unit_name}: the player's callsign, e.g. "Enfield 1-1";
-- - {type = "radar", airfields = {...}} what each airfield's radars detect (dcs/instructor_radar.lua, run in the
--   mission scripting environment), or {type = "radar", unavailable = reason} where that environment cannot be
--   reached (a client in someone else's multiplayer server);
-- - every 10 s, {type = "weather", airfields = {...}, mission = {...}}: wind, temperature, pressure, runways and ATC
--   frequencies of every airdrome, and the mission's visibility and clouds (dcs/instructor_weather.lua, same
--   environment as the radar);
-- - {type = "traffic", aircraft = [...]}: every aircraft of the mission, for the ATC agent
--   (dcs/instructor_traffic.lua, same environment);
-- - {type = "groups", ...} and {type = "flags", flags = {name = value}}: the agents' AI groups and the mission flags
--   they listen to, once the program asks for them on the task port (see below).
local INSTRUCTOR_DIR = assert(..., 'instructor_hook.lua needs the project folder as its argument')
package.path = package.path .. ';.\\LuaSocket\\?.lua'
package.cpath = package.cpath .. ';.\\LuaSocket\\?.dll'

local PORT = 47820
local CHECK_EVERY_S = 1.0
local WEATHER_EVERY_S = 10.0
local SCRIPT_STATES = {'server', 'mission'}   -- where the mission scripting API (world, coalition) may answer

local socket = require('socket')
local encode = dofile(INSTRUCTOR_DIR .. 'tools/dcs/json.lua')
local udp = socket.udp()
udp:settimeout(0)

local function log_info(text) log.write('DCS-Instructor', log.INFO, text) end
local function send(message) udp:sendto(encode(message), '127.0.0.1', PORT) end

local function read_file(path)
    local file = assert(io.open(path, 'r'))
    local text = file:read('*a')
    file:close()
    return text
end

-- A file built on the pilot's PC from the DCS install (tools/setup.py), so it may not exist yet: '' then.
local function read_optional(path)
    local file = io.open(path, 'r')
    if not file then log_info('not built yet: ' .. path); return '' end
    local text = file:read('*a')
    file:close()
    return text
end

-- Western callsign tables are {name index, flight, element, name = 'Enfield11'}: spoken "Enfield 1-1".
-- Eastern callsigns are plain numbers (e.g. 101).
local function format_callsign(callsign)
    if type(callsign) == 'number' then return tostring(callsign) end
    if type(callsign) == 'string' then return callsign end
    if type(callsign) ~= 'table' then return nil end
    local flight, element = callsign[2], callsign[3]
    local name = callsign.name and tostring(callsign.name):gsub('%d+$', '') or nil
    if name and name ~= '' and flight and element then return name .. ' ' .. flight .. '-' .. element end
    return callsign.name and tostring(callsign.name) or nil
end

-- The player's unit in the mission table, by mission id or by unit name.
local function find_mission_unit(mission, unit_id, unit_name)
    for _, side in pairs(mission and mission.coalition or {}) do
        for _, country in ipairs(side.country or {}) do
            for _, category in pairs({country.plane, country.helicopter}) do
                for _, group in ipairs(category and category.group or {}) do
                    for _, unit in ipairs(group.units or {}) do
                        if tostring(unit.unitId) == tostring(unit_id) or (unit_name and unit.name == unit_name) then
                            return unit
                        end
                    end
                end
            end
        end
    end
    return nil
end

local function player_unit()
    local unit_id = DCS.getPlayerUnit()
    if not unit_id or unit_id == '' then return nil end
    local unit_name = DCS.getUnitProperty(unit_id, DCS.UNIT_NAME)
    local unit = find_mission_unit(DCS.getCurrentMission() and DCS.getCurrentMission().mission, unit_id, unit_name)
    local callsign = unit and format_callsign(unit.callsign)
    if not callsign then callsign = format_callsign(DCS.getUnitProperty(unit_id, DCS.UNIT_CALLSIGN)) end
    return callsign, unit_name
end

-- Mission scripting environment -------------------------------------------------------------------------------
local script_state = nil      -- the net.dostring_in state that answers with the scripting API
local radar_loaded = false    -- this hook session sent its radar script (a mission may keep an older version)

local function run_in(state, code)
    local ok, result, success = pcall(net.dostring_in, state, code)
    if not ok or success == false then return nil end
    return result
end

local function find_script_state()
    for _, state in ipairs(SCRIPT_STATES) do
        if run_in(state, 'return type(world) == "table" and type(coalition) == "table" and "yes" or "no"') == 'yes' then
            return state
        end
    end
    return nil
end

local function radar_scan(unit_name)
    script_state = script_state or find_script_state()
    if not script_state then return nil, 'mission scripting environment not reachable (multiplayer client?)' end
    local call = 'if not DCSInstructorRadar then return "undefined" end return DCSInstructorRadar.scan(' ..
                 string.format('%q', unit_name or '') .. ')'
    local result = radar_loaded and run_in(script_state, call) or 'undefined'
    if result == 'undefined' then
        local code = 'DCSInstructorRadar_encode = (function() ' .. read_file(INSTRUCTOR_DIR .. 'tools/dcs/json.lua') ..
                     ' end)()\n' .. read_optional(INSTRUCTOR_DIR .. 'dcs/aircraft_rcs.lua') .. '\n' ..
                     read_file(INSTRUCTOR_DIR .. 'dcs/instructor_radar.lua') .. '\n' ..
                     read_file(INSTRUCTOR_DIR .. 'dcs/instructor_weather.lua') .. '\n' ..
                     read_file(INSTRUCTOR_DIR .. 'dcs/instructor_traffic.lua') .. '\n' ..
                     read_file(INSTRUCTOR_DIR .. 'dcs/instructor_tasks.lua') .. '\nreturn "ok"'
        if run_in(script_state, code) ~= 'ok' then return nil, 'radar script could not be loaded' end
        radar_loaded = true
        log_info('radar script loaded in the "' .. script_state .. '" environment')
        result = run_in(script_state, call)
    end
    if not result or result == '' then return nil, 'radar scan failed' end
    return result
end

-- Airfield weather (dcs/instructor_weather.lua), loaded with the radar script.
local function weather_read()
    if not (script_state and radar_loaded) then return nil end
    local result = run_in(script_state, 'if not DCSInstructorWeather then return "undefined" end ' ..
                                        'return DCSInstructorWeather.read()')
    if result == 'undefined' then radar_loaded = false; return nil end   -- reloaded with the next radar scan
    if not result or result == '' then return nil end
    local mission = run_in(script_state, 'return DCSInstructorWeather.mission()')
    return result, (mission and mission ~= '') and mission or 'null'
end

-- All mission traffic for the ATC (dcs/instructor_traffic.lua), loaded with the radar script.
local function traffic_read(unit_name)
    if not (script_state and radar_loaded) then return nil end
    local result = run_in(script_state, 'if not DCSInstructorTraffic then return "undefined" end ' ..
                                        'return DCSInstructorTraffic.read(' .. string.format('%q', unit_name or '') .. ')')
    if not result or result == '' or result == 'undefined' then return nil end
    return result
end

-- Mission tasks for the agents' AI groups (dcs/instructor_tasks.lua) ------------------------------------------------
-- Requests from the program (client/dcs_tasks.py) on 127.0.0.1:TASK_PORT, one line each: "<id>\t<unix time>\t<action>
-- \t<arg>...". Only the actions below, with their argument kinds; nothing received is ever run as code (the strings
-- are quoted with %q, numbers re-formatted). Answers {type = "task", id, ok, detail}; every second the watched groups'
-- status goes out as {type = "groups", groups, zones}.
local TASK_PORT, TASK_MAX_AGE_S = 47824, 10
local TASKS = {
    move = {'text', 'number', 'number', 'word'},          -- group, lat, lon, speed (slow/normal/fast)
    wait_pickup = {'text', 'number', 'number', 'number'}, -- group, lat, lon, zone radius m
    hold = {'text'},
    roe = {'text', 'word'},                               -- group, free/return/hold
    smoke = {'text', 'word'},                             -- group, colour
    attack = {'text', 'text'},                            -- group, enemy group (one the program saw it detect)
    drive = {'text', 'number', 'number', 'word', 'word', 'word'},   -- vehicles: lat, lon, speed, on/off road, formation
    carry_pickup = {'text', 'number', 'number', 'text', 'word'},    -- vehicle group, lat, lon, squad group, on/off road
    carry_dropoff = {'text', 'number', 'number', 'text', 'word'},   -- vehicle group, lat, lon, squad group, on/off road
    fire = {'text', 'number', 'number', 'number', 'number'},        -- artillery group, lat, lon, spread radius m, rounds
    cease_fire = {'text'},
    sail = {'text', 'number', 'number', 'number'},        -- ship group, lat, lon, speed m/s
    ship_hold = {'text'},
    steer = {'text', 'number', 'number', 'number'},       -- ship group, course true degrees, speed m/s, max metres
    ship_attack = {'text', 'text'},                       -- ship group, enemy group (one the program saw it detect)
    ship_aid = {'text', 'text', 'word', 'number', 'word', 'text', 'number'},
                                                          -- ship group, tacan/icls/link4/acls, on/off, channel, X/Y,
                                                          -- callsign, frequency MHz
    set_flag = {'flag', 'number'},                        -- flag, value; only a flag the program listed as settable
    -- Scripted wingmen (an AI aircraft group of the mission). 'player' is not sent: the hook puts the player's unit.
    wing_start = {'text', 'word', 'player'},              -- group, cold/hot/runway/air
    wing_follow = {'text', 'player', 'number', 'number', 'number'},   -- group, distance, interval, elevation (m)
    wing_escort = {'text', 'player'},
    wing_attack = {'text', 'text', 'word'},               -- group, enemy group, all/one (one unit of it)
    wing_engage_zone = {'text', 'number', 'number', 'number', 'word', 'number', 'number'},
                                                          -- group, lat, lon, radius m, ground/air, altitude m, speed
    wing_bomb = {'text', 'number', 'number', 'word', 'number', 'number', 'word'},
                                                          -- group, lat, lon, weapon, alt m, heading, dive/level/auto
    wing_fly = {'text', 'number', 'number', 'number', 'word', 'number'},
                                                          -- group, lat, lon, alt m, BARO/RADIO, speed m/s
    wing_orbit = {'text', 'number', 'number', 'number', 'number'},    -- group, lat, lon, altitude m, speed m/s
    wing_land = {'text', 'text'},                         -- group, airfield
    wing_land_at = {'text', 'number', 'number', 'number', 'word'},   -- group, lat, lon, seconds (0 = until told), combat/normal
    wing_pickup = {'text', 'number', 'number', 'text'},   -- helicopter group, lat, lon, squad group
    wing_dropoff = {'text', 'number', 'number', 'text'},  -- helicopter group, lat, lon, squad group
    wing_roe = {'text', 'word'},                          -- group, free/return/hold
}
local task_socket, watched = nil, ''
local last_unit_name = nil   -- the player's unit, for each squad's radio line of sight to it (status)
-- Mission flags: the program lists those the agents listen to (reported every second as {type = "flags", flags}) and
-- those their actions may set ("flags\t<watched>\t<settable>", '|' separated). No other flag can be set.
local watched_flags, settable_flags = '', {}

local function flag_names(text)
    local names = {}
    for name in (text or ''):gmatch('[^|]+') do
        if not (name:match('^[%w_%-]+$') and #name <= 40) then return nil end
        names[#names + 1] = name
    end
    return names
end

local function task_argument(kind, value)
    if kind == 'flag' then
        return settable_flags[value] and string.format('%q', value) or nil
    elseif kind == 'number' then
        local number = tonumber(value)
        return number and string.format('%.7f', number) or nil
    elseif kind == 'word' then
        return value:match('^[%a_]+$') and string.format('%q', value) or nil
    end
    return value:match('^[^%c]+$') and string.format('%q', value) or nil
end

local function task_reply(id, ok, detail)
    send({type = 'task', id = id, ok = ok, detail = detail})
end

local function task_request(line)
    local fields = {}
    for field in (line .. '\t'):gmatch('([^\t]*)\t') do fields[#fields + 1] = field end
    local id, created, action = fields[1], tonumber(fields[2]), fields[3]
    if not (id and id:match('^%w+$') and created and action) then return task_reply('?', false, 'malformed request') end
    if math.abs(os.time() - created) > TASK_MAX_AGE_S then return task_reply(id, false, 'request too old') end
    if action == 'watch' then   -- the groups whose status the program wants, '|' separated
        watched = (fields[4] or ''):gsub('|', '\n')
        return task_reply(id, true, 'watching')
    end
    if action == 'flags' then
        local watch, settable = flag_names(fields[4]), flag_names(fields[5])
        if not (watch and settable) then return task_reply(id, false, 'bad flag name') end
        watched_flags, settable_flags = table.concat(watch, '\n'), {}
        for _, name in ipairs(settable) do settable_flags[name] = true end
        return task_reply(id, true, 'flags listed')
    end
    local kinds = TASKS[action]
    if not kinds then return task_reply(id, false, 'action not allowed: ' .. action) end
    local args, field = {}, 4
    for i, kind in ipairs(kinds) do
        local arg
        if kind == 'player' then
            if not last_unit_name then return task_reply(id, false, 'the pilot is not in an aircraft') end
            arg = string.format('%q', last_unit_name)
        else
            arg = fields[field] and task_argument(kind, fields[field])
            if not arg and kind == 'flag' then return task_reply(id, false, 'flag not listed: ' .. tostring(fields[field])) end
            if not arg then return task_reply(id, false, 'bad argument ' .. (field - 3) .. ' for ' .. action) end
            field = field + 1
        end
        args[i] = arg
    end
    script_state = script_state or find_script_state()
    if not (script_state and radar_loaded) then return task_reply(id, false, 'mission scripting not ready') end
    local answer = run_in(script_state, 'return DCSInstructorTasks.' .. action .. '(' .. table.concat(args, ',') .. ')')
    local ok, detail = tostring(answer):match('"ok":(%a+)'), tostring(answer):match('"detail":"(.-)"')
    task_reply(id, ok == 'true', detail or tostring(answer))
end

local function task_receive()
    if not task_socket then
        task_socket = socket.udp()
        task_socket:settimeout(0)
        if not task_socket:setsockname('127.0.0.1', TASK_PORT) then
            task_socket = nil
            return
        end
        log_info('mission tasks listening on 127.0.0.1:' .. TASK_PORT)
    end
    for _ = 1, 4 do
        local line = task_socket:receive()
        if not line then break end
        local ok, err = pcall(task_request, line)
        if not ok then log_info('task request failed: ' .. tostring(err)) end
    end
end

local function groups_status()
    -- Also with no group watched while the player is in an aircraft: the player's wingmen ("flight").
    if (watched == '' and not last_unit_name) or not (script_state and radar_loaded) then return end
    local status = run_in(script_state, 'if not DCSInstructorTasks then return "" end return DCSInstructorTasks.status(' ..
                                        string.format('%q', watched) .. ',' ..
                                        string.format('%q', last_unit_name or '') .. ')')
    if status and status ~= '' then udp:sendto('{"type":"groups",' .. status:sub(2), '127.0.0.1', PORT) end
end

local function flags_status()
    if watched_flags == '' or not (script_state and radar_loaded) then return end
    local values = run_in(script_state, 'if not DCSInstructorTasks then return "" end return DCSInstructorTasks.flags(' ..
                                        string.format('%q', watched_flags) .. ')')
    if values and values ~= '' then udp:sendto('{"type":"flags","flags":' .. values .. '}', '127.0.0.1', PORT) end
end

-- Native command bridge (dcs/patches/radio_bridge.lua, patched into DCS's radio menu by client/dcs_patches.py) -------
-- DCS's GUI update callback never ran its tick in a mission (live 2026-11-02), so this hook drives it.
local BRIDGE_EVERY_S = 0.1
local next_bridge, bridge_logged = 0, false

local function bridge_state()
    local bridge = DCSAIServiceBridge   -- the panel's global (this hook's environment reads the state's globals)
    if type(bridge) ~= 'table' then
        local panel = package.loaded and package.loaded['RadioCommandDialogsPanel']
        bridge = type(panel) == 'table' and panel.DCSAIServiceBridge or nil
    end
    return type(bridge) == 'table' and type(bridge.tick) == 'function' and bridge or nil
end

local function bridge_tick(now)
    if now < next_bridge then return end
    next_bridge = now + BRIDGE_EVERY_S
    local bridge = bridge_state()
    if not bridge then return end
    if not bridge_logged then log_info('driving the native command bridge'); bridge_logged = true end
    local ok, err = pcall(bridge.tick)
    if not ok then log_info('bridge tick failed: ' .. tostring(err)) end
end

-- Callbacks ------------------------------------------------------------------------------------------------------
local sent_callsign, next_check, last_problem = nil, 0, nil
local next_weather, weather_logged, traffic_logged = 0, false, false
local hook = {}

function hook.onSimulationFrame()
    local now = DCS.getRealTime()
    bridge_tick(now)
    local received, problem_text = pcall(task_receive)
    if not received then log_info('tasks: ' .. tostring(problem_text)) end
    if now < next_check then return end
    next_check = now + CHECK_EVERY_S
    local listed, groups_problem = pcall(groups_status)
    if not listed then log_info('groups: ' .. tostring(groups_problem)) end
    local read_flags, flags_problem = pcall(flags_status)
    if not read_flags then log_info('flags: ' .. tostring(flags_problem)) end

    local ok, callsign, unit_name = pcall(player_unit)
    if not ok then log_info('callsign lookup failed: ' .. tostring(callsign)) callsign, unit_name = nil, nil end
    last_unit_name = unit_name
    if callsign then
        local key = callsign .. '|' .. tostring(unit_name)
        if key ~= sent_callsign then
            sent_callsign = key
            log_info('player callsign ' .. callsign .. ' (unit ' .. tostring(unit_name) .. ')')
        end
        -- Resent every check: the instructor may have started after the pilot took the slot. pilot: the player's
        -- name in DCS (multiplayer nickname), for the wingmen to know who they fly with.
        local got_name, pilot = pcall(function() return net.get_player_info(net.get_my_player_id(), 'name') end)
        send({type = 'callsign', callsign = callsign, unit_name = unit_name,
              pilot = got_name and type(pilot) == 'string' and pilot or nil})
    end

    local scanned, json, problem = pcall(radar_scan, unit_name)
    if not scanned then json, problem = nil, tostring(json) end
    if json then
        -- The scan is already JSON: wrap it without decoding.
        udp:sendto('{"type":"radar","airfields":' .. json .. '}', '127.0.0.1', PORT)
        last_problem = nil
    else
        if problem ~= last_problem then log_info('radar: ' .. tostring(problem)) end
        last_problem = problem
        send({type = 'radar', unavailable = problem})
    end

    local got, traffic = pcall(traffic_read, unit_name)
    if got and traffic then
        if not traffic_logged then log_info('mission traffic read'); traffic_logged = true end
        udp:sendto('{"type":"traffic",' .. traffic:sub(2), '127.0.0.1', PORT)   -- '{"aircraft":...}' unwrapped
    elseif not got then
        log_info('traffic: ' .. tostring(traffic))
    end

    if now >= next_weather then
        next_weather = now + WEATHER_EVERY_S
        local read, weather, mission = pcall(weather_read)
        if read and weather then
            if not weather_logged then log_info('airfield weather read'); weather_logged = true end
            udp:sendto('{"type":"weather","airfields":' .. weather .. ',"mission":' .. mission .. '}', '127.0.0.1', PORT)
        elseif not read then
            log_info('weather: ' .. tostring(weather))
        end
    end
end

function hook.onSimulationStop()
    sent_callsign, script_state, radar_loaded, next_weather, weather_logged = nil, nil, false, 0, false
    traffic_logged = false
end

DCS.setUserCallbacks(hook)
log_info('hook loaded: callsign and airfield radars to 127.0.0.1:' .. PORT)
