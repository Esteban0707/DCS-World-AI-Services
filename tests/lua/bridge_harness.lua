-- Runs dcs/patches/radio_bridge.lua outside DCS, as if appended to RadioCommandDialogsPanel.lua: the panel's locals
-- (base, data, selectAndTuneCommunicator), a fake LuaSocket, dxgui, coalition and Message. Prints what it sends.
-- Usage: luae.exe bridge_harness.lua <project dir with trailing slash> <case>
--   case "ready": requests arrive (see REQUESTS below); "no_menu": the radio menu is not initialised.
local project, case = assert(arg[1]), arg[2] or 'ready'
local NOW = 1000000
os.time = function() return NOW end

local queue = {}
local REQUESTS = {
    'a1\t' .. NOW .. '\tabort_takeoff\tSenaki-Kolkhi',
    'a2\t' .. NOW .. '\tabort_takeoff\tSenaki-Kolkhi',       -- repeated at once: refused
    'a3\t' .. NOW .. '\tinbound\tKutaisi',                    -- red airfield for a blue pilot: unavailable
    'a4\t' .. (NOW - 60) .. '\tinbound\tSenaki-Kolkhi',       -- too old
    'a5\t' .. NOW .. '\tlua_dostring\tSenaki-Kolkhi',         -- not in the list
    'garbage',
    'a6\t' .. NOW .. '\tembark_troops\tBuenos',
    'a7\t' .. NOW .. '\tembark_troops\tGhost',             -- not waiting for this helicopter
    'a8\t' .. NOW .. '\tdisembark_troops\tBuenos',
    'a9\t' .. NOW .. '\ttroops_on_board\t-',
    'w1\t' .. NOW .. '\twing_engage_bandits\t2',             -- the player's wingman 2
    'w2\t' .. NOW .. '\twing_wedge\t3',                      -- formations go to the whole flight
    'w3\t' .. NOW .. '\twing_cover_me\t4',                   -- no number 4 in this flight
    'w4\t' .. NOW .. '\twing_rejoin\tflight',
    'w5\t' .. NOW .. '\twing_cover_me\t7',                   -- not a wingman number
}
for _, line in ipairs(REQUESTS) do queue[#queue + 1] = line end
package.preload['socket'] = function()
    return {udp = function()
        return {settimeout = function() end, setsockname = function(_, host, port) print('BIND ' .. host .. ':' .. port) return 1 end,
                receive = function() return table.remove(queue, 1) end,
                sendto = function(_, data, host, port) print('REPLY ' .. port .. ' ' .. data) end, close = function() end}
    end}
end
local callbacks = {}
package.preload['dxgui'] = function()
    return {AddUpdateCallback = function(f) callbacks[#callbacks + 1] = f end,
            RemoveUpdateCallback = function(f)
                for i, g in ipairs(callbacks) do if g == f then table.remove(callbacks, i) break end end
            end,
            SetupApplicationUpdateCallback = function() print('SETUP') end,
            EnableHighSpeedUpdate = function(on) print('HIGH SPEED ' .. tostring(on)) end}
end
-- The panel's own initialize(), which DCS calls when it builds the radio menu for the player's unit.
function initialize() print('PANEL INIT') end
log = {INFO = 1, write = function(_, _, text) print('LOG ' .. text) end}
Message = {wMsgLeaderAbortTakeoff = 101, wMsgLeaderInbound = 102, wMsgLeaderAbortInbound = 103,
           wMsgLeaderRequestLanding = 104, wMsgLeaderRequestTaxiToRunway = 105, wMsgLeaderRequestTakeOff = 106,
           wMsgLeaderEngageBandits = 201, wMsgLeaderWedge = 202, wMsgLeaderCoverMe = 203, wMsgLeaderJoinUp = 204}
-- The player's group: the player and wingmen 2 and 3.
local group = {getUnit = function(_, n)
    return n <= 3 and {getCommunicator = function() return {name = 'Wingman ' .. n} end} or nil
end}
local function airbase(name)
    return {getName = function() return name end, getCommunicator = function() return {name = name} end}
end
local function descent(name)
    return {getName = function() return name end, getID = function() return 77 end,
            embarking = function(_, helicopter) print('EMBARK ' .. name .. ' on ' .. helicopter) end}
end
coalition = {side = {NEUTRAL = 0, RED = 1, BLUE = 2}, service = {ATC = 1},
             getAllDescents = function() return {descent('Buenos')} end,
             getDescentsOnBoard = function() return {descent('Buenos')} end,
             getServiceProviders = function(side) return side == 2 and {airbase('Senaki-Kolkhi')} or
                                                         side == 1 and {airbase('Kutaisi')} or {} end}

-- The panel's locals, as in RadioCommandDialogsPanel.lua.
local base = _G
local data = {initialized = case ~= 'no_menu', pUnit = {getCoalition = function() return 2 end,
              getObjectID = function() return 9001 end, getGroup = function() return group end,
              disembarking = function(_, id) print('DISEMBARK ' .. id) end},
              pComm = {sendRawMessage = function(_, command, parameters)
                  if type(parameters[1]) == 'table' then
                      print('SEND ' .. command .. ' to ' .. parameters[1].name)
                  else
                      local shown = {}
                      for i, p in ipairs(parameters) do shown[i] = tostring(p) end
                      print('SEND ' .. command .. ' with ' .. table.concat(shown, ','))
                  end
              end}}
local function selectAndTuneCommunicator(communicator) print('TUNE ' .. communicator.name) end

local file = assert(io.open(project .. 'dcs/patches/radio_bridge.lua'))
local source = file:read('*a')
file:close()
local chunk = assert(loadstring('local base, data, selectAndTuneCommunicator = ...\n' .. source))
chunk(base, data, selectAndTuneCommunicator)
print('CALLBACKS BEFORE INIT ' .. #callbacks)
initialize()
initialize()   -- the next mission initializes the panel again: still one callback
print('CALLBACKS ' .. #callbacks)
for _ = 1, 5 do for _, f in ipairs(callbacks) do f() end end
