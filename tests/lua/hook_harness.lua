-- Runs dcs/instructor_hook.lua outside DCS with a fake GUI-hook API and mission, and prints every UDP message.
-- Usage: luae.exe hook_harness.lua <project dir with trailing slash> <western|eastern|property|none> [radar]
--   western:  mission unit with callsign {1, 1, 1, name = 'Enfield11'} -> "Enfield 1-1"
--   eastern:  mission unit with callsign 101 -> "101"
--   property: unit not in the mission table; DCS.getUnitProperty(UNIT_CALLSIGN) gives "Ford21"
--   none:     no player unit (spectator / menus)
--   radar:    net.dostring_in('server') answers with a fake mission scripting API. Radars: unit "Senaki-Kolkhi-1"
--             (100 km air range) at the origin; group "Kutaisi EWR" (unit "Ground-2-1", no sensor data: default
--             range) 300 km north; group "Senaki-Kolkhi" also has a tank without radar. Aircraft: the player airborne
--             20 km north (seen), "Hidden" 30 km east behind terrain, "Far" 150 km south (out of range), "Taxiing"
--             on the ground. Without "radar", no state answers (multiplayer client).
local project = assert(arg[1], 'project dir')
local case = arg[2] or 'western'
local with_radar = arg[3] == 'radar'
-- arg[4]: "bridge_global" / "bridge_panel": the patched radio menu's bridge (dcs/patches/radio_bridge.lua) is found as
-- a global or in the panel's module table; the hook must drive its tick.
if arg[4] == 'bridge_global' then
    DCSAIServiceBridge = {tick = function() print('BRIDGE TICK') end}
elseif arg[4] == 'bridge_panel' then
    package.loaded['RadioCommandDialogsPanel'] = {DCSAIServiceBridge = {tick = function() print('BRIDGE TICK') end}}
end

log = {INFO = 1, ERROR = 2, write = function(_, _, text) print('LOG ' .. text) end}
-- Task requests the program would send to the hook's task port (only with arg[5] == "tasks").
TASK_REQUESTS = {}
if arg[5] == 'tasks' then
    local now = os.time()
    TASK_REQUESTS = {'t1\t' .. now .. '\twatch\tBuenos|Ghost', 't2\t' .. now .. '\tmove\tBuenos\t42.25\t42.04\tfast',
                     't3\t' .. now .. '\tdostring\tos.exit()', 't4\t' .. now .. '\tmove\tBuenos\tnorth\t42.04\tfast',
                     't5\t' .. (now - 60) .. '\thold\tBuenos', 't6\t' .. now .. '\tmove\tNobody\t42.25\t42.04\tslow'}
elseif arg[5] == 'wing' then   -- a scripted wingman: Manolo (late activation, parking at Senaki) and Lobo (in the air)
    local now = os.time()
    TASK_REQUESTS = {'w1\t' .. now .. '\twatch\tManolo', 'w2\t' .. now .. '\twing_follow\tManolo\t200\t200\t0',
                     'w3\t' .. now .. '\twing_start\tManolo\tsideways', 'w4\t' .. now .. '\twing_start\tManolo\tcold',
                     'w5\t' .. now .. '\twing_start\tManolo\tcold', 'w6\t' .. now .. '\twing_follow\tManolo\t300\t-150\t30',
                     'w7\t' .. now .. '\twing_bomb\tManolo\t42.1\t42.0\trockets\t150\t270\tdive', 'w8\t' .. now .. '\twing_start\tLobo\tcold',
                     'w9\t' .. now .. '\twing_start\tLobo\tair', 'w10\t' .. now .. '\twing_roe\tManolo\thold',
                     'w11\t' .. now .. '\twatch\tManolo|Paco|Halcon|Hip', 'w12\t' .. now .. '\twing_start\tPaco\tcold',
                     -- Hip: an uncontrolled Mi-8 while the leader flies an airplane (user, 2026-11-05).
                     'w20\t' .. now .. '\twing_start\tHip\tair', 'w21\t' .. now .. '\twing_start\tHip\tcold',
                     'w22\t' .. now .. '\twing_follow\tHip\t100\t100\t0',
                     'w23\t' .. now .. '\twing_land_at\tHip\t42.1\t42.0\t300\tcombat',
                     'w24\t' .. now .. '\twing_land_at\tManolo\t42.1\t42.0\t0\tnormal',
                     -- A helicopter attacks the ground only by strafing: a point, a ground group, a static.
                     'w25\t' .. now .. '\twing_bomb\tHip\t42.1\t42.0\trockets\t50\t-1\tdive',
                     'w26\t' .. now .. '\twing_attack\tHip\tRed Armor\tall',
                     'w27\t' .. now .. '\twing_attack\tHip\tStatic T-72B\tone',
                     -- Hip carries the squad Buenos (DCS's Embarking / Disembarking); an airplane cannot.
                     'w28\t' .. now .. '\twing_pickup\tHip\t42.1\t42.0\tBuenos',
                     'w29\t' .. now .. '\twing_dropoff\tHip\t42.2\t42.0\tBuenos',
                     'w30\t' .. now .. '\twing_pickup\tManolo\t42.1\t42.0\tBuenos',
                     'w13\t' .. now .. '\twing_start\tPaco\tcold',
                     'w14\t' .. now .. '\twing_attack\tManolo\tBuenos\tone',
                     'w15\t' .. now .. '\twing_engage_zone\tManolo\t42.1\t42.0\t3000\tground\t1500\t150',
                     'w16\t' .. now .. '\twing_attack\tManolo\tStatic T-72B\tone',
                     'w17\t' .. now .. '\twing_fly\tManolo\t42.1\t42.0\t60\tRADIO\t200',
                     'w18\t' .. now .. '\twing_attack\tManolo\tRed SAM\tall',
                     'w19\t' .. now .. '\twing_attack\tManolo\tRed Armor\tall'}
elseif arg[5] == 'ground' then   -- vehicles (user, 2026-11-05): Bison, two BTR-80s, drives and carries Buenos
    local now = os.time()
    TASK_REQUESTS = {'g1\t' .. now .. '\twatch\tBuenos|Bison',
                     'g2\t' .. now .. '\tdrive\tBison\t42.25\t42.04\tfast\ton\twedge',
                     'g3\t' .. now .. '\tdrive\tBison\t42.25\t42.04\tslow\toff\tkeep',
                     'g4\t' .. now .. '\tdrive\tBison\t42.25\t42.04\tslow\toff\tsquare',
                     'g5\t' .. now .. '\tcarry_pickup\tBison\t42.1\t42.0\tBuenos\toff',
                     'g6\t' .. now .. '\tcarry_dropoff\tBison\t42.2\t42.0\tBuenos\ton',
                     'g7\t' .. now .. '\tcarry_pickup\tBuenos\t42.1\t42.0\tBison\toff'}
elseif arg[5] == 'flags' then   -- the agents' Triggers: watch 10 and convoy_left, may set 20 and smoke_ready
    local now = os.time()
    TASK_REQUESTS = {'f1\t' .. now .. '\tflags\t10|convoy_left\t20|smoke_ready', 'f2\t' .. now .. '\tset_flag\t20\t1',
                     'f3\t' .. now .. '\tset_flag\t10\t1', 'f4\t' .. now .. '\tflags\t10;os.exit()\t'}
end
-- Mission flags in the fake scripting environment: flag 10 is already on.
local user_flags = {['10'] = 1}
package.preload['socket'] = function()
    return {udp = function()
        return {settimeout = function() end, sendto = function(_, data) print('SENT ' .. data) end,
                setsockname = function(_, host, port) print('BIND ' .. host .. ':' .. port) return 1 end,
                receive = function() return TASKS_OPEN and table.remove(TASK_REQUESTS, 1) or nil end}
    end}
end

local callsigns = {western = {1, 1, 1, name = 'Enfield11'}, eastern = 101}
local mission = {coalition = {blue = {country = {{plane = {group = {{units = {
    {unitId = 7, name = 'Aerial-1-1', callsign = callsigns[case]}}}}}}}}}}
local clock, callbacks = 0, nil
DCS = {
    UNIT_NAME = 'name', UNIT_CALLSIGN = 'callsign',
    getRealTime = function() return clock end,
    getPlayerUnit = function() if case == 'none' then return nil end return case == 'property' and '99' or '7' end,
    getUnitProperty = function(_, property)
        if property == 'name' then return case == 'property' and 'Client-9' or 'Aerial-1-1' end
        if property == 'callsign' then return 'Ford21' end
    end,
    getCurrentMission = function() return {mission = mission} end,
    setUserCallbacks = function(hook) callbacks = hook end,
}

-- Fake mission scripting environment ------------------------------------------------------------------------------
local function object(fields)
    local o = {isExist = function() return true end, isActive = function() return true end}
    for k, v in pairs(fields) do o[k] = v end
    return o
end
-- rcs: what DCS's getDesc() gives (nil: not given, the radar script uses dcs/aircraft_rcs.lua or its default).
local function aircraft(name, type_name, point, flying, rcs, side)
    return object({getName = function() return name end, getTypeName = function() return type_name end,
                   getCallsign = function() return name == 'Aerial-1-1' and 'Enfield11' or 'Uzi11' end,
                   inAir = function() return flying end, getPoint = function() return point end,
                   getVelocity = function() return {x = 100, y = 0, z = 0} end,
                   getDesc = function() return {RCS = rcs} end, getCoalition = function() return side or 2 end})
end
local player = aircraft('Aerial-1-1', 'F-5E-3', {x = 20000, y = 914.4, z = 0}, true)
-- Senaki's radar sees 100 km for the reference 5 m2 (upper hemisphere, head-on = tail-on): a 0.5 m2 drone only
-- 56 km, an 80 m2 transport 200 km.
local flying = {player, aircraft('Hidden', 'Su-25T', {x = 0, y = 300, z = 30000}, true),
                aircraft('Far', 'Su-25T', {x = -150000, y = 3000, z = 0}, true),
                aircraft('Taxiing', 'F-5E-3', {x = 500, y = 20, z = 0}, false),
                aircraft('Drone', 'MQ-9 Reaper', {x = 90000, y = 3000, z = 0}, true, 0.5),
                aircraft('Transport', 'C-130', {x = 140000, y = 3000, z = 0}, true, 80),
                aircraft('Bandit', 'MiG-29A', {x = -60000, y = 5000, z = 0}, true, 5, 1)}
local function radar_unit(name, point, sensors, detected)
    return object({getName = function() return name end, getPoint = function() return point end,
                   hasSensors = function(_, sensor) return sensor == 1 end, getSensors = function() return sensors end,
                   getCoalition = function() return 2 end,
                   getController = function() return {getDetectedTargets = function()
                       local list = {}
                       for _, unit in ipairs(detected or {}) do list[#list + 1] = {object = unit} end
                       return list
                   end} end})
end
local units = {
    ['Senaki-Kolkhi-1'] = radar_unit('Senaki-Kolkhi-1', {x = 0, y = 20, z = 0}, {[1] = {{detectionDistanceAir = {
        upperHemisphere = {headOn = 100000, tailOn = 100000}, lowerHemisphere = {headOn = 60000, tailOn = 60000}}}}},
        {flying[7]}),   -- its AI detects the enemy MiG-29
    ['Ground-2-1'] = radar_unit('Ground-2-1', {x = 300000, y = 20, z = 0}, {}),
    ['Tank-1'] = object({getName = function() return 'Tank-1' end, hasSensors = function() return false end}),
}
local function group(name, members)
    return {getName = function() return name end, getUnits = function() return members end,
            isExist = function() return true end, getID = function() return 40 end}
end
-- Red ground units for a wingman's eyes (the wingmen sit at 0,0 facing east, +z): Armor 3 km north (beside it: seen),
-- Rear 3 km behind (hidden by its own aircraft), Far 8 km (beyond a vehicle's 6 km), Ridge 3 km east-southeast but
-- behind terrain (z > 10 km is hidden in this fake, so 11 km away: out of range anyway) - and SAM 9 km (big: seen).
local function red(name, x, z, type_name, sam)
    return object({getName = function() return name end, getLife = function() return 1 end,
                   getPoint = function() return {x = x, y = 13, z = z} end, getTypeName = function() return type_name end,
                   getDesc = function() return {category = 2} end, hasSensors = function() return false end,
                   hasAttribute = function(_, attribute) return sam == true and attribute == 'SAM elements' end})
end
-- Red statics (live 2026-11-04: the targets of zone L-1 were static objects): a parked T-72B 2 km north (seen), a
-- building 2.5 km behind (not seen).
local red_statics = {red('Static T-72B', 2000, 500, 'T-72B'), red('Static Building', 0, -2500, 'Building08_PBR')}
local function red_group(name, members)
    local g = group(name, members)
    g.getCategory = function() return 2 end   -- Group.Category.GROUND
    return g
end
local red_ground = {red_group('Red Armor', {red('Armor-1', 3000, 0, 'T-72B'), red('Armor-2', 3100, 0, 'T-72B')}),
                    red_group('Red Rear', {red('Rear-1', 0, -3000, 'BTR-80')}),
                    red_group('Red Far', {red('Far-1', 8000, 0, 'BMP-2')}),
                    red_group('Red SAM', {red('SAM-1', 0, 9000, 'Kub 1S91 str', true)})}
-- Weather: Senaki-Kolkhi (airdrome, elevation 13 m) has wind from the west (air moving east, 5 m/s), 15 C, 1012 hPa
-- at the field, runway "09" (course -90 deg = true heading 90); "FARP-1" is not an airdrome.
-- Senaki-Kolkhi's communicator has two radios (261.0 and 132.0 MHz); Kutaisi gives none.
local function airbase(name, category, runways, radios)
    return {getName = function() return name end, getDesc = function() return {category = category} end,
            getPoint = function() return {x = 0, y = 13, z = 0} end, getRunways = function() return runways end,
            getCoalition = function() return 2 end,
            getCommunicator = function()
                if not radios then return nil end
                return {hasTransiver = function() return true end, countTransivers = function() return #radios end,
                        getFrequency = function(_, i) return radios[i + 1] end}
            end}
end
-- Scripted wingmen: Manolo (parking at Senaki, late activation) and Lobo (placed in the air); inactive until started.
-- Paco: UNCONTROLLED on its parking spot (exists and is active, asleep until DCS's Start command).
-- Halcon: started by the mission itself. Hip: an uncontrolled Mi-8 (a helicopter group).
local wing_started, wing_groups = {Paco = true, Halcon = true, Hip = true}, {}
local function wing_group(name)
    if wing_groups[name] then return wing_groups[name] end
    local function pos(task)
        local p = task.params.pos
        return p and (' leader ' .. task.params.groupId .. ' pos ' .. p.x .. ',' .. p.y .. ',' .. p.z) or ''
    end
    local controller = {
        setTask = function(_, task)
            local route = task.id == 'Mission' and task.params.route.points
            local first = route and route[1].task and route[1].task.params.tasks
            if task.id == 'Embarking' or task.id == 'Disembarking' then
                local p = task.params
                print('WING TASK ' .. name .. ' ' .. task.id .. ' at ' .. p.x .. ',' .. p.y .. ' groups ' ..
                      table.concat(p.groupsForEmbarking, '+'))
                return
            end
            if task.id == 'Land' then
                local p = task.params
                print('WING TASK ' .. name .. ' Land at ' .. p.point.x .. ',' .. p.point.y .. ' duration ' ..
                      tostring(p.durationFlag) .. ' ' .. tostring(p.duration) .. ' combat ' .. tostring(p.combatLandingFlag))
                return
            end
            print('WING TASK ' .. name .. ' ' .. task.id .. pos(task) .. (route and (' points ' .. #route ..
                  ' first tasks ' .. table.concat((function()
                      local ids = {}
                      for _, t in ipairs(first or {}) do ids[#ids + 1] = t.id end
                      return ids
                  end)(), '+')) or ''))
        end,
        pushTask = function(_, task)
            local p = task.params or {}
            print('WING PUSH ' .. name .. ' ' .. task.id .. ((task.id == 'Bombing' or task.id == 'Strafing') and
                  (' weapon ' .. tostring(p.weaponType) .. ' alt ' .. tostring(p.altitude) .. ' dir ' ..
                   (p.direction and string.format('%.2f', p.direction) or 'nil') .. ' ' .. tostring(p.attackType))
                  or ''))
        end,
        setCommand = function(_, command) print('WING COMMAND ' .. name .. ' ' .. command.id) end,
        setOption = function(_, id, value) print('WING OPTION ' .. name .. ' ' .. id .. '=' .. value) end,
        -- Manolo's RWR picks up an SA-8's radar north-east of it, too far to be seen: all methods give it, the
        -- located ones (visual, optic, radar, IRST, datalink) do not.
        getDetectedTargets = function(_, ...)
            if name ~= 'Manolo' or select('#', ...) > 0 then return {} end
            local emitter_group
            local emitter = {isExist = function() return true end, getLife = function() return 1 end,
                             getName = function() return 'Osa-1-1' end, getTypeName = function() return 'Osa 9A33 ln' end,
                             getPoint = function() return {x = 30000, y = 20, z = 30000} end,
                             getDesc = function() return {category = 2} end,
                             getGroup = function() return emitter_group end}
            emitter_group = {getName = function() return 'Osa-1' end, getUnits = function() return {emitter} end}
            return {{object = emitter}}
        end}
    local group
    local unit = {isExist = function() return true end, isActive = function() return wing_started[name] == true end,
                  getLife = function() return 1 end, getLife0 = function() return 1 end, getFuel = function() return 0.9 end,
                  getPoint = function() return {x = 0, y = 13, z = 0} end,
                  getVelocity = function() return {x = 0, y = 0, z = 0} end,
                  getPosition = function() return {x = {x = 0, y = 0, z = 1}} end,
                  inAir = function() return false end, getTypeName = function() return 'F-5E-3' end,
                  getCoalition = function() return 2 end,
                  getAmmo = function() return {{count = 2, desc = {displayName = 'Mk-82', category = 3}}} end,
                  getController = function() return controller end, getGroup = function() return group end}
    group = {isExist = function() return true end, getName = function() return name end,
             getCategory = function() return name == 'Hip' and 1 or 0 end, getUnits = function() return {unit} end,
             getUnit = function() return unit end, getInitialSize = function() return 1 end,
             getController = function() return controller end, getID = function() return 5 end}
    wing_groups[name] = group
    return group
end
units['Aerial-1-1'] = object({getPoint = function() return {x = 20000, y = 914.4, z = 0} end,
                              getCoalition = function() return 2 end,
                              getVelocity = function() return {x = 100, y = 0, z = 0} end,
                              getPosition = function() return {x = {x = 1, y = 0, z = 0}} end,
                              getGroup = function() return {getID = function() return 9 end,
                                                            getName = function() return 'Colt' end} end})
local scripting = setmetatable({
    AI = {Option = {Air = {id = {ROE = 0}, val = {ROE = {WEAPON_FREE = 0, OPEN_FIRE = 2, RETURN_FIRE = 3,
                                                          WEAPON_HOLD = 4}}}}},
    world = {event = {S_EVENT_SHOT = 1, S_EVENT_HIT = 2, S_EVENT_TAKEOFF = 3, S_EVENT_LAND = 4, S_EVENT_BIRTH = 15,
                      S_EVENT_ENGINE_STARTUP = 18, S_EVENT_KILL = 28},
             addEventHandler = function() print('WING EVENTS HANDLER') end,
             -- The pilot's F10 marks: "LZ1" (blue, a second line of notes), a red one, an empty one.
             getMarkPanels = function()
                 return {{idx = 1, coalition = 2, text = '  LZ1 \nnotes', pos = {x = 5000, y = 0, z = 100}},
                         {idx = 2, coalition = 1, text = 'Red camp', pos = {x = 0, y = 0, z = 0}},
                         {idx = 3, coalition = -1, text = '', pos = {x = 0, y = 0, z = 0}}}
             end,
             getAirbases = function()
        return {airbase('Senaki-Kolkhi', 0, {{Name = '09', course = -math.pi / 2, length = 2400, width = 45,
                                              position = {x = 0, y = 13, z = 0}}}, {261000000, 132000000}),
                airbase('Kutaisi', 0, {}), airbase('FARP-1', 1, {})}
    end},
    Airbase = {Category = {AIRDROME = 0, HELIPAD = 1, SHIP = 2}},
    atmosphere = {getWind = function() return {x = 0, y = 0, z = 5} end,
                  getTemperatureAndPressure = function() return 288.15, 101200 end},
    coalition = {side = {NEUTRAL = 0, RED = 1, BLUE = 2}, addGroup = function(country, category, data)
        local first, task = data.route.points[1], data.route.points[1].task
        local follow = task and task.params.tasks[1]
        print('ADD GROUP ' .. data.name .. ' country ' .. country .. ' category ' .. category .. ' start ' .. first.type ..
              ' / ' .. first.action .. ' late ' .. tostring(data.lateActivation) .. ' unit at ' ..
              math.floor(data.units[1].x) .. ',' .. math.floor(data.units[1].y) ..
              (follow and (' then ' .. follow.id .. ' leader ' .. follow.params.groupId) or '') ..
              ' points ' .. #data.route.points)
        wing_started[data.name] = true
    end, getGroups = function(side, category)
        if side == 2 and category == 2 then
            return {group('Senaki-Kolkhi', {units['Senaki-Kolkhi-1'], units['Tank-1']}),
                    group('Kutaisi EWR', {units['Ground-2-1']})}
        end
        if side == 2 and category == 0 then return {group('Colt', flying)} end
        if side == 1 and category == 2 and arg[5] == 'wing' then return red_ground end
        return {}
    end, getStaticObjects = function(side)
        if side == 1 and arg[5] == 'wing' then return red_statics end
        return {}
    end},
    StaticObject = {getByName = function(name)
        for _, static in ipairs(red_statics) do if static:getName() == name then return static end end
        return nil
    end},
    -- Terrain hides whatever lies east of the radars (z > 10 km).
    land = {isVisible = function(_, target) return target.z < 10000 end, getHeight = function() return 13 end},
    Group = {Category = {AIRPLANE = 0, HELICOPTER = 1, GROUND = 2, SHIP = 3}, getByName = function(name)
        if name == 'Manolo' or name == 'Lobo' or name == 'Paco' or name == 'Halcon' or name == 'Hip' then
            return wing_group(name)
        end
        for _, red_group in ipairs(red_ground) do if red_group:getName() == name then return red_group end end
        if name == 'Bison' then   -- two BTR-80s: vehicles with a troop compartment (DCS answers getDescentCapacity)
            local btr = {getPoint = function() return {x = 300, y = 20, z = 400} end,
                         getVelocity = function() return {x = 0, y = 0, z = 0} end,
                         isExist = function() return true end, getLife = function() return 1 end,
                         getTypeName = function() return 'BTR-80' end,
                         hasAttribute = function(_, attribute) return attribute == 'APC' end,
                         getDescentCapacity = function() return 700 end, getDescentOnBoard = function() return 0, 0 end}
            return {isExist = function() return true end, getCategory = function() return 2 end,
                    getID = function() return 8 end, getName = function() return 'Bison' end,
                    getUnit = function() return btr end, getUnits = function() return {btr, btr} end,
                    getInitialSize = function() return 2 end,
                    getController = function() return {
                        setTask = function(_, task)
                            local points = task.params.route.points
                            local last = points[#points]
                            local combo = last.task and last.task.params.tasks[1]
                            print('GROUND TASK Bison ' .. task.id .. ' ' .. last.action .. ' to ' .. last.x .. ',' ..
                                  last.y .. ' at ' .. last.speed .. (combo and (' then ' .. combo.id .. ' groups ' ..
                                  table.concat(combo.params.groupsForEmbarking, ',')) or ''))
                        end,
                        setOption = function(_, id, value) print('GROUND OPTION Bison ' .. id .. '=' .. value) end} end}
        end
        if name ~= 'Buenos' then return nil end
        local leader = {getPoint = function() return {x = 100, y = 20, z = 200} end,
                        getVelocity = function() return {x = 1, y = 0, z = 0} end,
                        isExist = function() return true end, getLife = function() return 1 end,
                        getID = function() return 31 end, getTypeName = function() return 'Soldier M4' end,
                        hasAttribute = function(_, attribute) return attribute == 'Infantry' end}
        return {isExist = function() return true end, getCategory = function() return 2 end,
                getID = function() return 7 end,
                getUnit = function() return leader end, getUnits = function() return {leader, leader} end,
                getInitialSize = function() return 4 end,
                getController = function() return {setTask = function(_, task)
                    print('TASK ' .. task.id .. ' to ' .. task.params.route.points[2].x .. ',' ..
                          task.params.route.points[2].y .. ' at ' .. task.params.route.points[2].speed)
                end} end}
    end},
    Unit = {Category = {AIRPLANE = 0, HELICOPTER = 1, GROUND_UNIT = 2}, SensorType = {RADAR = 1},
            getByName = function(name) return units[name] end},
    Controller = {Detection = {VISUAL = 1, OPTIC = 2, RADAR = 4, IRST = 8, RWR = 16, DLINK = 32}},
    coord = {LOtoLL = function() return 42.1, 42.0, 914.4 end,
             LLtoLO = function(lat, lon) return {x = lat * 1000, y = 0, z = lon * 1000} end},
    env = {info = function(text) print('ENV ' .. text) end,
           mission = {coalition = {blue = {country = {{id = 2, plane = {group = {
                          {name = 'Manolo', lateActivation = true, task = 'CAS', units = {{name = 'Manolo-1', x = 10, y = 20, parking = 5}},
                           route = {points = {{type = 'TakeOffParkingHot', action = 'From Parking Area Hot',
                                               airdromeId = 23, x = 10, y = 20}}}},
                          {name = 'Lobo', lateActivation = true, units = {{name = 'Lobo-1', x = 0, y = 0, alt = 3000}},
                           route = {points = {{type = 'Turning Point', action = 'Turning Point', x = 0, y = 0,
                                               alt = 3000}}}},
                          {name = 'Halcon', task = 'CAS', units = {{name = 'Halcon-1', x = 50, y = 60, parking = 7}},
                           route = {points = {{type = 'TakeOffParkingHot', action = 'From Parking Area Hot',
                                               airdromeId = 23, x = 50, y = 60}}}},
                          {name = 'Paco', uncontrolled = true, units = {{name = 'Paco-1', x = 30, y = 40, parking = 6}},
                           -- Like the user's Viper (2026-11-04): only the departure point, with the editor's CAP task.
                           route = {points = {{type = 'TakeOffParking', action = 'From Parking Area',
                                               airdromeId = 23, x = 30, y = 40,
                                               task = {id = 'ComboTask', params = {tasks = {
                                                   [1] = {number = 1, id = 'EngageTargets', key = 'CAP'}}}}}}}}}},
                          helicopter = {group = {
                          {name = 'Hip', uncontrolled = true, task = 'Transport', units = {{name = 'Hip-1', x = 70, y = 80, parking = 8}},
                           route = {points = {{type = 'TakeOffParking', action = 'From Parking Area', airdromeId = 23,
                                               x = 70, y = 80}}}}}}}}}},
                      triggers = {zones = {{name = 'LZ North', x = 5000, y = 300, radius = 150}}}, weather = {visibility = {distance = 80000}, enable_fog = true, fog = {visibility = 3000,
                      thickness = 200}, clouds = {preset = 'Preset7', base = 1200}, enable_dust = false}}},
    timer = {getTime = function() return clock end, getAbsTime = function() return 43200 + clock end},
    trigger = {misc = {getUserFlag = function(name) return user_flags[tostring(name)] or 0 end},
               action = {setUserFlag = function(name, value)
                   print('FLAG ' .. tostring(name) .. ' = ' .. tostring(value))
                   user_flags[tostring(name)] = value
               end}},
}, {__index = _G})

net = {dostring_in = function(state, code)
    if not with_radar or state ~= 'server' then return '', false end
    local chunk, err = loadstring(code)
    if not chunk then return err, false end
    setfenv(chunk, scripting)
    local ok, result = pcall(chunk)
    return tostring(result), ok
end}

-- Loaded the way DCSInstructorHook.lua loads it: the folder as the argument, no INSTRUCTOR_DIR global (DCS runs
-- each hook file in its own environment, so a global set there never reaches the script).
assert(INSTRUCTOR_DIR == nil)
assert(loadfile(project .. 'dcs/instructor_hook.lua'))(project)
for frame = 1, arg[5] == 'wing' and 12 or 3 do
    clock = clock + 0.6
    TASKS_OPEN = frame > 1   -- requests arrive once the mission scripts are loaded
    if frame == 5 and scripting.DCSInstructorWingEvents then   -- DCS reports Manolo's takeoff
        local manolo = wing_group('Manolo'):getUnit(1)
        local events = scripting.DCSInstructorWingEvents
        events:onEvent({id = 3, initiator = manolo})
        -- Then a bomb away, a tank destroyed, and a hit taken from a ZU-23 (combat log for the callouts).
        events:onEvent({id = 1, initiator = manolo,
                        weapon = {getDesc = function() return {displayName = 'Mk-82'} end}})
        events:onEvent({id = 28, initiator = manolo, target = {getTypeName = function() return 'T-72B' end,
                                                               getDesc = function() return {category = 2} end}})
        events:onEvent({id = 2, initiator = {getTypeName = function() return 'ZU-23' end}, target = manolo})
    end
    callbacks.onSimulationFrame()
end
