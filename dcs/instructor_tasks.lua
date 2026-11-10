-- DCS-Instructor mission tasks, for agents that command AI ground groups (e.g. a squad the pilot carries). Runs inside
-- DCS's mission scripting environment, sent there by dcs/instructor_hook.lua together with dcs/instructor_radar.lua
-- (which provides DCSInstructorRadar_encode).
--
-- The model never sends Lua: our Python code picks one of these functions and its arguments (atc-style tools), the
-- hook checks them against a fixed list and calls the function. Each function checks again here (group exists, is a
-- ground group) and returns JSON {ok = true/false, detail}. Positions come as lat/lon and are turned into map x/z.
-- Also reads and sets the mission's flags for the agents' Triggers (events: flag on -> the agent calls; actions: the
-- agent sets a flag the mission's triggers act on).
-- Task tables follow DCS's own mission editor definitions (MissionEditor/modules/me_action_db.lua, read 2026-11-02):
-- EmbarkToTransport {x, y, zoneRadius} makes infantry walk to a point and wait for a helicopter; the player's
-- helicopter then embarks them through DCS's radio menu (dcs/patches/radio_bridge.lua, "embark_troops").
DCSInstructorTasks = {}
local T = DCSInstructorTasks
local SPEEDS = {slow = 2, normal = 4, fast = 7}   -- m/s on foot or off road

local function encode(value) return DCSInstructorRadar_encode(value) end
local function result(ok, detail) return encode({ok = ok, detail = detail}) end

local function ground_group(name)
    local group = Group.getByName(name)
    if not group or not group:isExist() then return nil, 'no group called ' .. name end
    if group:getCategory() ~= Group.Category.GROUND then return nil, name .. ' is not a ground group' end
    local leader = group:getUnit(1)
    if not leader then return nil, name .. ' has no units left' end
    return group, leader
end

local function point_of(lat, lon)
    local p = coord.LLtoLO(tonumber(lat), tonumber(lon), 0)
    return {x = p.x, y = p.z}   -- tasks use x north and y east (map z)
end

-- Walk or drive to a point, off road, in a loose formation.
function T.move(name, lat, lon, speed)
    local group, leader = ground_group(name)
    if not group then return result(false, leader) end
    local from, to = leader:getPoint(), point_of(lat, lon)
    local ms = SPEEDS[speed] or SPEEDS.normal
    local route = {points = {
        {x = from.x, y = from.z, type = 'Turning Point', action = 'Off Road', speed = ms},
        {x = to.x, y = to.y, type = 'Turning Point', action = 'Off Road', speed = ms},
    }}
    group:getController():setTask({id = 'Mission', params = {route = route}})
    return result(true, 'moving')
end

-- Walk to a point and wait there to be picked up by a helicopter.
function T.wait_pickup(name, lat, lon, radius)
    local group, leader = ground_group(name)
    if not group then return result(false, leader) end
    local to = point_of(lat, lon)
    group:getController():setTask({id = 'EmbarkToTransport',
                                   params = {x = to.x, y = to.y, zoneRadius = tonumber(radius) or 200}})
    return result(true, 'waiting for pickup')
end

-- Stop where they are.
function T.hold(name)
    local group, leader = ground_group(name)
    if not group then return result(false, leader) end
    group:getController():setCommand({id = 'StopRoute', params = {value = true}})
    return result(true, 'holding')
end

-- Rules of engagement: free (weapons free), return (return fire), hold (weapons hold).
function T.roe(name, value)
    local ship = Group.getByName(name)
    local naval = ship and ship:isExist() and ship:getCategory() == Group.Category.SHIP   -- ships: the naval option
    local group, leader = ship, nil
    if not naval then
        group, leader = ground_group(name)
        if not group then return result(false, leader) end
    end
    local option = naval and AI.Option.Naval or AI.Option.Ground
    local values = {free = option.val.ROE.OPEN_FIRE, ['return'] = option.val.ROE.RETURN_FIRE,
                    hold = option.val.ROE.WEAPON_HOLD}
    if not values[value] then return result(false, 'unknown rules of engagement ' .. tostring(value)) end
    group:getController():setOption(option.id.ROE, values[value])
    return result(true, 'rules of engagement ' .. value)
end

-- Smoke at the group's position: green, red, white, orange, blue.
function T.smoke(name, color)
    local group, leader = ground_group(name)
    if not group then return result(false, leader) end
    local colors = {green = trigger.smokeColor.Green, red = trigger.smokeColor.Red, white = trigger.smokeColor.White,
                    orange = trigger.smokeColor.Orange, blue = trigger.smokeColor.Blue}
    if not colors[color] then return result(false, 'unknown smoke color ' .. tostring(color)) end
    trigger.action.smoke(leader:getPoint(), colors[color])
    return result(true, color .. ' smoke')
end

-- Ground vehicles (user, 2026-11-05: armour is ordered like a squad, on or off the road, in a formation; some carry
-- infantry). From DCS's own data: route point actions "On Road" / "Off Road" (MissionEditor/modules/me_mission.lua)
-- and the vehicle formations of its radio menu for a ground unit (Scripts/UI/RadioCommandDialogPanel/Config/
-- groundUnit.lua: OPTION_FORMATION 5, Column 1 ... EchelonL 7).
local VEHICLE_SPEEDS = {slow = 5, normal = 10, fast = 17}   -- m/s
local OPTION_FORMATION = 5
local FORMATIONS = {column = 1, row = 2, wedge = 3, vee = 4, diamond = 5, echelon_right = 6, echelon_left = 7}

local function is_infantry(unit)
    local ok, infantry = pcall(function() return unit:hasAttribute('Infantry') end)
    return ok and infantry == true
end

local function road_action(road) return road == 'on' and 'On Road' or 'Off Road' end

-- A route from the leader to a point; the last point may carry a task (as the Mission Editor puts a waypoint action).
local function route_to(leader, to, action, ms, task)
    local from = leader:getPoint()
    local last = {x = to.x, y = to.y, type = 'Turning Point', action = action, speed = ms}
    if task then
        last.task = {id = 'ComboTask', params = {tasks = {{id = task.id, number = 1, enabled = true, auto = false,
                                                           params = task.params}}}}
    end
    return {id = 'Mission', params = {route = {points = {
        {x = from.x, y = from.z, type = 'Turning Point', action = action, speed = ms}, last}}}}
end

-- Drive to a point: on the road (DCS finds the way along the roads) or off road, slow/normal/fast, in a formation
-- (column, row, wedge, vee, diamond, echelon_right, echelon_left; "keep" leaves it as it is).
function T.drive(name, lat, lon, speed, road, formation)
    local group, leader = ground_group(name)
    if not group then return result(false, leader) end
    if formation ~= 'keep' and not FORMATIONS[formation] then
        return result(false, 'unknown formation ' .. tostring(formation))
    end
    -- speed: slow/normal/fast, or km/h as a number (user, 2026-11-06: speed control for the vehicles)
    local kmh = tonumber(speed)
    local ms = VEHICLE_SPEEDS[speed] or (kmh and math.max(1, math.min(kmh, 100)) / 3.6) or VEHICLE_SPEEDS.normal
    local controller = group:getController()
    controller:setTask(route_to(leader, point_of(lat, lon), road_action(road), ms))
    if FORMATIONS[formation] then
        pcall(function() controller:setOption(OPTION_FORMATION, FORMATIONS[formation]) end)
    end
    return result(true, 'driving ' .. (road == 'on' and 'on the road' or 'off road') ..
                        (FORMATIONS[formation] and (' in ' .. formation:gsub('_', ' ')) or ''))
end

-- A vehicle carries a squad (user, 2026-11-05: "vehicles in DCS can pick up infantry"): DCS's Embarking and
-- Disembarking, which its Mission Editor offers a vehicle as it does a helicopter (me_action_db.lua 'vehicle' tasks,
-- only for a type with InternalCargo: BTR-80, MaxxPro...). It drives there and the troops get in (they wait with
-- EmbarkToTransport, T.wait_pickup) or out. squad: the ground group's name.
local function ground_transport(name, lat, lon, squad, id, road)
    local group, leader = ground_group(name)
    if not group then return nil, leader end
    if is_infantry(leader) then return nil, 'infantry carries no troops' end
    local troops = Group.getByName(squad or '')
    if not troops or not troops:isExist() then return nil, 'no group called ' .. tostring(squad) end
    local to = point_of(lat, lon)
    local params = {x = to.x, y = to.y, groupsForEmbarking = {troops:getID()}}
    if id == 'Embarking' then
        params.durationFlag, params.duration, params.distributionFlag, params.distribution = false, 300, false, {}
    end
    group:getController():setTask(route_to(leader, to, road_action(road), VEHICLE_SPEEDS.normal,
                                           {id = id, params = params}))
    env.info('DCS-Instructor: vehicle ' .. name .. ' ' .. id .. ' ' .. squad .. ' at ' .. tostring(lat) .. ', ' ..
             tostring(lon))
    return group
end

function T.carry_pickup(name, lat, lon, squad, road)
    local group, problem = ground_transport(name, lat, lon, squad, 'Embarking', road)
    if not group then return result(false, problem) end
    return result(true, 'driving there to pick up ' .. squad)
end

function T.carry_dropoff(name, lat, lon, squad, road)
    local group, problem = ground_transport(name, lat, lon, squad, 'Disembarking', road)
    if not group then return result(false, problem) end
    return result(true, 'driving there to drop ' .. squad)
end

-- Attack an enemy group the squad sees: weapons free and move onto its first living unit; DCS's AI fights on contact.
function T.attack(name, target)
    local group, leader = ground_group(name)
    if not group then return result(false, leader) end
    local enemy = Group.getByName(target)
    if not enemy or not enemy:isExist() then
        -- A static object (a parked tank, a truck, a building): fire at its point (DCS's FireAtPoint, as the
        -- Mission Editor's ground "Fire at point" task); the crews shoot from where they are.
        local oks, static = pcall(StaticObject.getByName, target)
        if not oks or not static or not static:isExist() then return result(false, 'no group called ' .. target) end
        local p = static:getPoint()
        local controller = group:getController()
        controller:setOption(AI.Option.Ground.id.ROE, AI.Option.Ground.val.ROE.OPEN_FIRE)
        controller:setTask({id = 'FireAtPoint', params = {point = {x = p.x, y = p.z}, radius = 15}})
        return result(true, 'firing at the ' .. static:getTypeName() .. ' ' .. target)
    end
    local aim = nil
    for _, unit in ipairs(enemy:getUnits() or {}) do
        if unit:isExist() and unit:getLife() > 0 then aim = unit; break end
    end
    if not aim then return result(false, target .. ' has no units left') end
    local from, to = leader:getPoint(), aim:getPoint()
    local controller = group:getController()
    local ms = is_infantry(leader) and SPEEDS.normal or VEHICLE_SPEEDS.normal
    controller:setOption(AI.Option.Ground.id.ROE, AI.Option.Ground.val.ROE.OPEN_FIRE)
    controller:setTask({id = 'Mission', params = {route = {points = {
        {x = from.x, y = from.z, type = 'Turning Point', action = 'Off Road', speed = ms},
        {x = to.x, y = to.z, type = 'Turning Point', action = 'Off Road', speed = ms},
    }}}})
    return result(true, 'attacking ' .. target)
end

-- The enemy groups a group's soldiers see or hear (their AI's own detection: realistic, enemies only), nearest unit
-- of each group: {group, units, type, lat, lon}. At most ENEMIES_SHOWN groups.
-- Detected by the radar warning receiver only (user, 2026-11-04: an Osa called at 21 miles, with its exact place,
-- that nobody could see): a real RWR gives the emitter's direction, not where it is: {group, type, air, rwr = true,
-- bearing_deg} and no position.
local ENEMIES_SHOWN = 5
-- A ground group or a ship (both may fire at a point, set their rules of engagement, stop).
local function surface_group(name)
    local group = Group.getByName(name)
    if not group or not group:isExist() then return nil, 'no group called ' .. name end
    local category = group:getCategory()
    if category ~= Group.Category.GROUND and category ~= Group.Category.SHIP then
        return nil, name .. ' is not a ground group or a ship'
    end
    local leader = group:getUnit(1)
    if not leader then return nil, name .. ' has no units left' end
    return group, leader
end

-- Ships (user, 2026-11-07: "mobile artillery that sails to the coordinates we give it, at a speed"). From DCS's own
-- data (MissionEditor/modules/me_action_db.lua 'ship': tasks FireAtPoint, AttackGroup, ...; naval commands
-- ActivateBeacon, ActivateICLS, ActivateLink4, ActivateACLS and their Deactivate ones).
local function ship_group(name)
    local group = Group.getByName(name)
    if not group or not group:isExist() then return nil, 'no group called ' .. name end
    if group:getCategory() ~= Group.Category.SHIP then return nil, name .. ' is not a ship group' end
    local leader = group:getUnit(1)
    if not leader then return nil, name .. ' has no ships left' end
    return group, leader
end

-- Water all the way: the route is checked every SEA_CHECK_M (a ship cannot sail over land).
local SEA_CHECK_M = 500
local function at_sea(x, z)
    local kind = land.getSurfaceType({x = x, y = z})
    return kind == land.SurfaceType.WATER or kind == land.SurfaceType.SHALLOW_WATER
end

-- Sail straight to a point at a speed (m/s).
function T.sail(name, lat, lon, speed)
    local group, leader = ship_group(name)
    if not group then return result(false, leader) end
    local from, to = leader:getPoint(), point_of(lat, lon)
    local dx, dz = to.x - from.x, to.y - from.z
    local length = math.sqrt(dx * dx + dz * dz)
    local steps = math.max(1, math.floor(length / SEA_CHECK_M))
    for i = 1, steps do
        local x, z = from.x + dx * i / steps, from.z + dz * i / steps
        if not at_sea(x, z) then
            return result(false, string.format('land in the way %.1f km along that course: give another point',
                                               length * i / steps / 1000))
        end
    end
    local ms = math.max(0.5, tonumber(speed) or 8)
    group:getController():setTask({id = 'Mission', params = {route = {points = {
        {x = from.x, y = from.z, type = 'Turning Point', action = 'Turning Point', speed = ms},
        {x = to.x, y = to.y, type = 'Turning Point', action = 'Turning Point', speed = ms}}}}})
    return result(true, string.format('sailing, %.0f knots', ms * 1.943844))
end

-- Steer a course (true degrees) at a speed (m/s) with no point given (user, 2026-11-07: "Trident, course 027 at 15
-- knots"): the route goes up to max_m along the course, or, with land ahead, to SHORE_MARGIN_M short of it.
local SHORE_MARGIN_M = 1000
function T.steer(name, course, speed, max_m)
    local group, leader = ship_group(name)
    if not group then return result(false, leader) end
    local from = leader:getPoint()
    local angle = math.rad(tonumber(course) or 0)
    local nx, nz = math.cos(angle), math.sin(angle)   -- x north, z east
    local limit = math.max(SEA_CHECK_M, tonumber(max_m) or 100000)
    local reach, land_at = limit, nil
    for d = SEA_CHECK_M, limit, SEA_CHECK_M do
        if not at_sea(from.x + nx * d, from.z + nz * d) then land_at = d; break end
    end
    if land_at then reach = land_at - SHORE_MARGIN_M end
    if reach < SEA_CHECK_M then
        return result(false, string.format('land %.1f km ahead on that course: give another course', (land_at or 0) / 1000))
    end
    local ms = math.max(0.5, tonumber(speed) or 8)
    group:getController():setTask({id = 'Mission', params = {route = {points = {
        {x = from.x, y = from.z, type = 'Turning Point', action = 'Turning Point', speed = ms},
        {x = from.x + nx * reach, y = from.z + nz * reach, type = 'Turning Point', action = 'Turning Point',
         speed = ms}}}}})
    -- The hook passes only ok and detail: the distance goes in the words ("for 41.3 km").
    return result(true, string.format('steering, %.0f knots, for %.1f km%s', ms * 1.943844, reach / 1000,
                                      land_at and ', then it stops 1 km short of the coast ahead' or ' of open sea'))
end

-- Stop the ships where they are.
function T.ship_hold(name)
    local group, leader = ship_group(name)
    if not group then return result(false, leader) end
    local p = leader:getPoint()
    group:getController():setTask({id = 'Mission', params = {route = {points = {
        {x = p.x, y = p.z, type = 'Turning Point', action = 'Turning Point', speed = 0}}}}})
    return result(true, 'all stop')
end

-- Attack an enemy group (another ship, a ground group) it detects: DCS's AttackGroup.
function T.ship_attack(name, target)
    local group, leader = ship_group(name)
    if not group then return result(false, leader) end
    local enemy = Group.getByName(target)
    if not enemy or not enemy:isExist() then return result(false, 'no group called ' .. target) end
    local controller = group:getController()
    pcall(function() controller:setOption(AI.Option.Naval.id.ROE, AI.Option.Naval.val.ROE.OPEN_FIRE) end)
    controller:setTask({id = 'AttackGroup', params = {groupId = enemy:getID()}})
    return result(true, 'engaging ' .. target)
end

-- Navigation aids aboard the lead ship, as the Mission Editor's naval commands give them: kind tacan / icls / link4 /
-- acls, on or off. TACAN: channel, X/Y, callsign, frequency_mhz (worked out by the program as the editor does,
-- calcTACANFrequencyMHz), system 3 (a ship's TACAN: fill_TACAN). ICLS: channel. Link 4: frequency_mhz.
function T.ship_aid(name, kind, on, channel, mode, callsign, frequency_mhz)
    local group, leader = ship_group(name)
    if not group then return result(false, leader) end
    local unit_id = leader:getID()
    local command
    if on ~= 'on' then
        local off = {tacan = 'DeactivateBeacon', icls = 'DeactivateICLS', link4 = 'DeactivateLink4',
                     acls = 'DeactivateACLS'}
        if not off[kind] then return result(false, 'unknown aid ' .. tostring(kind)) end
        command = {id = off[kind], params = {}}
    elseif kind == 'tacan' then
        command = {id = 'ActivateBeacon', params = {type = 4, system = 3, AA = false, bearing = true,
                   channel = math.floor(tonumber(channel) or 1), modeChannel = mode == 'Y' and 'Y' or 'X',
                   callsign = callsign, name = callsign, frequency = (tonumber(frequency_mhz) or 0) * 1000000,
                   unitId = unit_id}}
    elseif kind == 'icls' then
        command = {id = 'ActivateICLS', params = {type = 131584, channel = math.floor(tonumber(channel) or 1),
                   unitId = unit_id}}
    elseif kind == 'link4' then
        command = {id = 'ActivateLink4', params = {unitId = unit_id, frequency = (tonumber(frequency_mhz) or 336) *
                   1000000}}
    elseif kind == 'acls' then
        command = {id = 'ActivateACLS', params = {unitId = unit_id}}
    else
        return result(false, 'unknown aid ' .. tostring(kind))
    end
    group:getController():setCommand(command)
    return result(true, kind .. ' ' .. (on == 'on' and 'on' or 'off'))
end

-- What a ship group is (for its agent): heading, an aircraft carrier or not and its aids (DCS's unit attributes, as the
-- Mission Editor checks them: "Aircraft Carriers", "Link4", "ACLS"), and the wind where it is (atmosphere.getWind:
-- where the wind blows to, m/s), for turning into the wind.
-- The aids the mission maker gave the ship (user, 2026-11-07: "if the editor assigns a TACAN, does the agent see it?"):
-- the naval commands in its route's waypoint actions, read as the Mission Editor itself lists them (route.points[].task
-- .params.tasks[].params.action, me_action_db.lua). Read once per group.
local AID_COMMANDS = {ActivateBeacon = 'tacan', ActivateICLS = 'icls', ActivateLink4 = 'link4', ActivateACLS = 'acls'}
T.mission_aids = T.mission_aids or {}
local function mission_ship_aids(name)
    if T.mission_aids[name] ~= nil then return T.mission_aids[name] or nil end
    local found = false
    local ok, coalitions = pcall(function() return env.mission.coalition end)
    for _, side in pairs(ok and type(coalitions) == 'table' and coalitions or {}) do
        for _, country in ipairs(side.country or {}) do
            for _, g in ipairs((country.ship or {}).group or {}) do
                if g.name == name then
                    found = {}
                    for _, point in ipairs((g.route or {}).points or {}) do
                        local tasks = point.task and point.task.params and point.task.params.tasks or {}
                        for _, task in ipairs(tasks) do
                            local action = task.params and task.params.action
                            local kind = action and AID_COMMANDS[action.id]
                            if kind and task.enabled ~= false then
                                local p = action.params or {}
                                found[kind] = {channel = p.channel, mode = p.modeChannel, callsign = p.callsign,
                                               mhz = p.frequency and p.frequency / 1000000 or nil}
                            end
                        end
                    end
                end
            end
        end
    end
    T.mission_aids[name] = found
    return found or nil
end

local function ship_details(leader)
    local forward = leader:getPosition().x
    local p = leader:getPoint()
    local out = {heading_deg = math.deg(math.atan2(forward.z, forward.x)) % 360}
    local got_aids, aids = pcall(mission_ship_aids, leader:getGroup():getName())
    if got_aids and aids and next(aids) then out.mission_aids = aids end
    for key, attribute in pairs({carrier = 'Aircraft Carriers', link4 = 'Link4', acls = 'ACLS'}) do
        local ok, has = pcall(function() return leader:hasAttribute(attribute) end)
        out[key] = ok and has == true
    end
    local okw, wind = pcall(atmosphere.getWind, {x = p.x, y = p.y + 20, z = p.z})
    if okw and wind then
        out.wind_to_deg = math.deg(math.atan2(wind.z, wind.x)) % 360
        out.wind_ms = math.sqrt(wind.x * wind.x + wind.z * wind.z)
    end
    return out
end

-- Artillery (user, 2026-11-07: Archer, a BM-21): DCS's "Fire at point" task as its Mission Editor gives it to a ground
-- group (me_action_db.lua; Scripts/GeneratedTasks/modules/artillery_battery_60_blue.lua: FireAtPoint with x, y,
-- zoneRadius, expendQty, expendQtyEnabled): the rounds fall spread over a circle of radius metres around the point.
-- rounds 0 = as many as the AI decides (DCS's default); otherwise that many.
function T.fire(name, lat, lon, radius, rounds)
    local group, leader = surface_group(name)   -- a battery or a ship
    if not group then return result(false, leader) end
    local to = point_of(lat, lon)
    local count = math.floor(tonumber(rounds) or 0)
    local params = {point = {x = to.x, y = to.y}, x = to.x, y = to.y, radius = tonumber(radius) or 100,
                    zoneRadius = tonumber(radius) or 100, expendQtyEnabled = count > 0, expendQty = math.max(count, 1)}
    local controller = group:getController()
    local option = group:getCategory() == Group.Category.SHIP and AI.Option.Naval or AI.Option.Ground
    controller:setOption(option.id.ROE, option.val.ROE.OPEN_FIRE)
    controller:setTask({id = 'FireAtPoint', params = params})
    return result(true, 'fire mission given: ' .. (count > 0 and (count .. ' rounds') or 'rounds as needed') ..
                        ', spread ' .. math.floor(params.radius) .. ' m')
end

-- Stop firing: an empty task (the group stays where it is).
function T.cease_fire(name)
    local group, leader = surface_group(name)
    if not group then return result(false, leader) end
    group:getController():resetTask()
    return result(true, 'cease fire')
end

local function detected_objects(controller)
    local all = controller:getDetectedTargets()
    local D = Controller.Detection
    local located = {}   -- by unit name: DCS hands a new table for the same object on each call
    for _, target in ipairs(controller:getDetectedTargets(D.VISUAL, D.OPTIC, D.RADAR, D.IRST, D.DLINK) or {}) do
        local ok, name = pcall(function() return target.object:getName() end)
        if ok and name then located[name] = true end
    end
    return all or {}, located
end

local function enemies_seen_by(units)
    local found, order = {}, {}
    for _, unit in ipairs(units) do
        if unit:isExist() and unit:getLife() > 0 then
            local ok, targets, located = pcall(detected_objects, unit:getController())
            for _, target in ipairs(ok and targets or {}) do
                local object = target.object
                local okg, enemy = pcall(function()
                    return object and object:isExist() and object.getGroup and object:getGroup()
                end)
                if okg and enemy then
                    local enemy_name = enemy:getName()
                    local rwr_only = not located[object:getName()]
                    if (not found[enemy_name] or (found[enemy_name].rwr and not rwr_only)) and
                        (found[enemy_name] or #order < ENEMIES_SHOWN) then
                        local p = object:getPoint()
                        local alive = 0
                        for _, u in ipairs(enemy:getUnits() or {}) do
                            if u:isExist() and u:getLife() > 0 then alive = alive + 1 end
                        end
                        local okc, desc = pcall(function() return object:getDesc() end)
                        local category = okc and desc and desc.category   -- 0 airplane, 1 helicopter, 2 ground, 3 ship
                        local entry = {group = enemy_name, type = object:getTypeName(),
                                       air = category == 0 or category == 1}
                        if rwr_only then
                            local me = unit:getPoint()
                            entry.rwr = true
                            entry.bearing_deg = math.floor(math.deg(math.atan2(p.z - me.z, p.x - me.x)) % 360 + 0.5)
                        else
                            entry.units, entry.alt_m = alive, p.y
                            entry.lat, entry.lon = coord.LOtoLL(p)
                        end
                        if not found[enemy_name] then order[#order + 1] = enemy_name end
                        found[enemy_name] = entry
                    end
                end
            end
        end
    end
    local list = {}
    for _, enemy_name in ipairs(order) do list[#list + 1] = found[enemy_name] end
    return list
end

local function enemies_of(group)
    return enemies_seen_by(group:getUnits() or {})
end

-- A wingman's own eyes (user's request, 2026-11-04: DCS's AI detection of an aircraft almost never reports ground
-- targets): what a pilot in that aircraft could see, as the airfield radars are our own model (instructor_radar.lua).
-- A unit is seen within EYES_RANGE_M of its kind, not in the blind sector behind the aircraft, with no terrain in
-- between (land.isVisible), and not farther than the mission's visibility or, below the fog top, its fog visibility.
-- Enemies and friends (other coalition / own coalition), nearest unit of each group: {group, units, type, lat, lon,
-- alt_m, air, distance_m}, nearest groups first.
local EYES_RANGE_M = {[0] = 15000, [1] = 10000, [2] = 6000, [3] = 10000}   -- airplane, helicopter, ground, ship
local EYES_SAM_M = 10000            -- a SAM site (radars, launchers) is big and easy to spot
local EYES_BLIND_DEG = 150          -- more than this off the nose (horizontally): hidden behind the aircraft
local EYES_SHOWN = 14             -- a target area may hold a dozen statics
local EYES_CATEGORIES = {Group.Category.AIRPLANE, Group.Category.HELICOPTER, Group.Category.GROUND,
                         Group.Category.SHIP}

local function visibility_m(target_agl)
    local ok, weather = pcall(function() return env.mission.weather end)
    if not ok or type(weather) ~= 'table' then return nil end
    local limit = weather.visibility and tonumber(weather.visibility.distance)
    if weather.enable_fog and weather.fog and target_agl < (tonumber(weather.fog.thickness) or 0) then
        local fog = tonumber(weather.fog.visibility)
        if fog then limit = math.min(limit or fog, fog) end
    end
    return limit
end

local function eyes_of(unit)
    local okc, side = pcall(function() return unit:getCoalition() end)
    if not okc or not side then return {}, {} end
    local p = unit:getPoint()
    local forward = unit:getPosition().x
    local heading = math.atan2(forward.z, forward.x)
    local found = {enemy = {}, friend = {}}
    local sides = {[side] = 'friend', [side == 1 and 2 or 1] = 'enemy'}   -- neutrals are not looked at
    local own_group = unit:getGroup():getName()
    for other_side, kind in pairs(sides) do
        for _, category in ipairs(EYES_CATEGORIES) do
            for _, group in ipairs(coalition.getGroups(other_side, category) or {}) do
                local name = group:getName()
                if name ~= own_group then
                    local alive, best = 0, nil
                    for _, target in ipairs(group:getUnits() or {}) do
                        local oka, living = pcall(function() return target:isExist() and target:getLife() > 0 end)
                        if oka and living then pcall(function()
                            alive = alive + 1
                            local q = target:getPoint()
                            local dx, dz = q.x - p.x, q.z - p.z
                            local d = math.sqrt(dx * dx + dz * dz)
                            local okd, desc = pcall(function() return target:getDesc() end)
                            local cat = okd and desc and desc.category or 2
                            local range = EYES_RANGE_M[cat] or EYES_RANGE_M[2]
                            local oks, sam = pcall(function() return target:hasAttribute('SAM elements') end)
                            if oks and sam then range = math.max(range, EYES_SAM_M) end
                            local agl = q.y - land.getHeight({x = q.x, y = q.z})
                            local limit = visibility_m(agl)
                            if limit then range = math.min(range, limit) end
                            local off = math.abs(math.deg((math.atan2(dz, dx) - heading + math.pi) % (2 * math.pi)
                                                          - math.pi))
                            if d <= range and (best == nil or d < best.distance_m) and
                                (d < 300 or off <= EYES_BLIND_DEG) and
                                land.isVisible({x = p.x, y = p.y, z = p.z}, {x = q.x, y = q.y + 2, z = q.z}) then
                                local lat, lon = coord.LOtoLL(q)
                                best = {group = name, type = target:getTypeName(), lat = lat, lon = lon, alt_m = q.y,
                                        air = cat == 0 or cat == 1, distance_m = d, seen = 'eyes'}
                            end
                        end) end   -- a unit DCS cannot describe is skipped, never the whole look
                    end
                    if best then
                        best.units = alive
                        found[kind][#found[kind] + 1] = best
                    end
                end
            end
        end
        -- Static objects (live 2026-11-04: the targets of zone L-1 were statics: T-72B, BTR-80, trucks, buildings),
        -- each its own entry, named by its Mission Editor name.
        local okl, statics = pcall(coalition.getStaticObjects, other_side)
        for _, static in ipairs(okl and statics or {}) do
            pcall(function()
                if not static:isExist() or static:getLife() <= 0 then return end
                local q = static:getPoint()
                local dx, dz = q.x - p.x, q.z - p.z
                local d = math.sqrt(dx * dx + dz * dz)
                local range = EYES_RANGE_M[2]
                local agl = q.y - land.getHeight({x = q.x, y = q.z})
                local limit = visibility_m(agl)
                if limit then range = math.min(range, limit) end
                local off = math.abs(math.deg((math.atan2(dz, dx) - heading + math.pi) % (2 * math.pi) - math.pi))
                if d <= range and (d < 300 or off <= EYES_BLIND_DEG) and
                    land.isVisible({x = p.x, y = p.y, z = p.z}, {x = q.x, y = q.y + 2, z = q.z}) then
                    local lat, lon = coord.LOtoLL(q)
                    found[kind][#found[kind] + 1] = {group = static:getName(), type = static:getTypeName(), units = 1,
                                                     lat = lat, lon = lon, alt_m = q.y, air = false, static = true,
                                                     distance_m = d, seen = 'eyes'}
                end
            end)
        end
    end
    for _, list in pairs(found) do
        table.sort(list, function(a, b) return a.distance_m < b.distance_m end)
        while #list > EYES_SHOWN do table.remove(list) end
    end
    return found.enemy, found.friend
end

-- Enemy static objects a vehicle crew sees (user, 2026-11-06: Axe never called its targets, they were statics, which
-- DCS's AI detection of a ground unit never reports): within STATIC_SEEN_M, terrain clear from the crew's eyes
-- (CREW_EYES_M up), not beyond the mission's visibility. Turret crews look all round: no blind sector. Infantry too
-- (user, 2026-11-07: Tango never saw the statics), from a standing soldier's eyes (SOLDIER_EYES_M), all round.
local STATIC_SEEN_M = 4000
local CREW_EYES_M = 2.5
local SOLDIER_EYES_M = 1.7
-- Ships too (user, 2026-11-07): lookouts on the bridge, BRIDGE_EYES_M up, with binoculars out to SHIP_STATIC_SEEN_M.
local BRIDGE_EYES_M = 25
local SHIP_STATIC_SEEN_M = 8000
local function statics_seen_by(leader, eyes_m, range_m)
    local STATIC_SEEN_M = range_m or STATIC_SEEN_M
    local side = leader:getCoalition()
    local p = leader:getPoint()
    local eye = {x = p.x, y = p.y + (eyes_m or CREW_EYES_M), z = p.z}
    local list = {}
    local okl, statics = pcall(coalition.getStaticObjects, side == 1 and 2 or 1)
    for _, static in ipairs(okl and statics or {}) do
        pcall(function()
            if not static:isExist() or static:getLife() <= 0 then return end
            local q = static:getPoint()
            local d = math.sqrt((q.x - p.x) ^ 2 + (q.z - p.z) ^ 2)
            local limit = visibility_m(q.y - land.getHeight({x = q.x, y = q.z}))
            if d <= math.min(STATIC_SEEN_M, limit or STATIC_SEEN_M) and
                land.isVisible(eye, {x = q.x, y = q.y + 2, z = q.z}) then
                local lat, lon = coord.LOtoLL(q)
                list[#list + 1] = {group = static:getName(), type = static:getTypeName(), units = 1, lat = lat,
                                   lon = lon, alt_m = q.y, air = false, static = true, distance_m = d}
            end
        end)
    end
    table.sort(list, function(a, b) return a.distance_m < b.distance_m end)
    while #list > ENEMIES_SHOWN do table.remove(list) end
    return list
end

-- DCS's own detection plus the eyes: one entry per enemy group (DCS's first), at most EYES_SHOWN; an emitter only on
-- the RWR last, and only when its group is not seen otherwise.
local function merge_enemies(detected, seen)
    local out, names, located, rwr = {}, {}, {}, {}
    for _, enemy in ipairs(detected or {}) do
        local list = enemy.rwr and rwr or located
        list[#list + 1] = enemy
    end
    for _, list in ipairs({located, seen or {}, rwr}) do
        for _, enemy in ipairs(list) do
            if not names[enemy.group] and #out < EYES_SHOWN then
                names[enemy.group] = true
                out[#out + 1] = enemy
            end
        end
    end
    return out
end

-- The player's wingmen (units 2 to 4 of the player's group; 2026-11-03): what each one knows of itself (damage, fuel,
-- weapons left), where it is, what its own sensors detect (enemies only) and whether the terrain blocks the radio
-- path to the player. A wingman that was in the group and is gone has exists = false.
local WEAPON_CATEGORY = {[0] = 'gun', [1] = 'missile', [2] = 'rocket', [3] = 'bomb'}

-- The radio of a flight as the Mission Editor set it (group frequency in MHz, modulation 0 AM / 1 FM): DCS's AI
-- wingmen hear their leader's orders only on it (live 2026-11-03: the player on 361.000, orders shown on screen,
-- the wingman did nothing).
local function mission_radio(group_name)
    local ok, coalitions = pcall(function() return env.mission.coalition end)
    if not ok or type(coalitions) ~= 'table' then return nil end
    for _, side in pairs(coalitions) do
        for _, country in ipairs(side.country or {}) do
            for _, kind in ipairs({'plane', 'helicopter'}) do
                for _, g in ipairs((country[kind] or {}).group or {}) do
                    if g.name == group_name and tonumber(g.frequency) then
                        return {mhz = tonumber(g.frequency), modulation = tonumber(g.modulation) or 0}
                    end
                end
            end
        end
    end
    return nil
end

local function flight_radio(player_name)
    if not player_name or player_name == '' then return nil end
    local ok, player = pcall(Unit.getByName, player_name)
    if not ok or not player or not player:isExist() or not player:getGroup() then return nil end
    return mission_radio(player:getGroup():getName())
end
local function flight_of(player_name)
    if not player_name or player_name == '' then return nil end
    local ok, player = pcall(Unit.getByName, player_name)
    if not ok or not player or not player:isExist() then return nil end
    local group = player:getGroup()
    if not group then return nil end
    local size = group:getInitialSize() or 1
    local me = player:getPoint()
    local wingmen = {}
    for number = 2, math.min(size, 4) do
        local unit = group:getUnit(number)
        local entry = {number = number, exists = false}
        if unit and unit:isExist() and unit ~= player then
            local p, v = unit:getPoint(), unit:getVelocity()
            local lat, lon = coord.LOtoLL(p)
            local weapons = {}
            for _, ammo in ipairs(unit:getAmmo() or {}) do
                local desc = ammo.desc or {}
                weapons[#weapons + 1] = {name = desc.displayName or desc.typeName or '?', count = ammo.count or 0,
                                         kind = WEAPON_CATEGORY[desc.category] or 'other'}
            end
            entry = {number = number, exists = true, type = unit:getTypeName(), life = unit:getLife(),
                     life0 = unit:getLife0(), fuel = unit:getFuel(), in_air = unit:inAir(), lat = lat, lon = lon,
                     alt_m = p.y, vx = v.x, vz = v.z, weapons = weapons,
                     radio = {line_of_sight = land.isVisible(p, me),
                              distance_m = math.sqrt((p.x - me.x) ^ 2 + (p.z - me.z) ^ 2)}}
            local seen_ok, enemies = pcall(enemies_seen_by, {unit})
            if seen_ok and #enemies > 0 then entry.enemies = enemies end
        end
        wingmen[#wingmen + 1] = entry
    end
    return wingmen
end

-- Scripted wingmen (user's design, 2026-11-04): a SEPARATE AI aircraft group placed in the Mission Editor with late
-- activation (e.g. "Manolo"). The pilot starts it by radio, cold or hot on its parking spot, on the runway, or in the
-- air beside the leader; then it gets script tasks, never DCS's F1 menu (which did nothing in the A-4E-C, 2026-11-03).
-- Task tables from DCS's own editor data (MissionEditor/modules/me_action_db.lua, read 2026-11-04): Follow / Escort
-- pos = {x = -distance, y = elevation, z = interval (+ right)}, Escort engagementDistMax 60000, AttackGroup, Bombing,
-- Orbit. A started group is re-spawned from its own Mission Editor entry with coalition.addGroup (same name: DCS
-- replaces the inactive group) and the first waypoint's start type changed as asked.
local START_TYPES = {cold = {'TakeOffParking', 'From Parking Area'}, hot = {'TakeOffParkingHot', 'From Parking Area Hot'},
                     runway = {'TakeOff', 'From Runway'}}
-- Numbers by kind of aircraft (user, 2026-11-05: the same wingman for helicopters, the Mi-8 first): where an air start
-- puts it beside the leader, its lowest air-start speed, and the second point of a departure route (distance, height,
-- speed; a helicopter's height is above the ground).
local KIND = {
    airplane = {behind = 1000, right = 300, min_speed = 130, above_ground = 300,
                next_m = 20000, next_alt = 2000, next_alt_type = 'BARO', next_speed = 200},
    helicopter = {behind = 200, right = 100, min_speed = 30, above_ground = 50,
                  next_m = 5000, next_alt = 150, next_alt_type = 'RADIO', next_speed = 50},
}
local FOLLOW_DEFAULT = {x = -200, y = 0, z = 200}   -- 200 m behind, 200 m to the right ("a mis cuatro")
T.wings = T.wings or {}   -- group name -> {mode, leader, events = {name = model time}, last}

local function copy(value)
    if type(value) ~= 'table' then return value end
    local out = {}
    for k, v in pairs(value) do out[k] = copy(v) end
    return out
end

-- The group's own entry in the Mission Editor: country id, category, a copy of its table.
local function mission_aircraft_group(name)
    local ok, coalitions = pcall(function() return env.mission.coalition end)
    if not ok or type(coalitions) ~= 'table' then return nil end
    for _, side in pairs(coalitions) do
        for _, country in ipairs(side.country or {}) do
            for kind, category in pairs({plane = Group.Category.AIRPLANE, helicopter = Group.Category.HELICOPTER}) do
                for _, g in ipairs((country[kind] or {}).group or {}) do
                    if g.name == name then return country.id, category, copy(g) end
                end
            end
        end
    end
    return nil
end

local function air_group(name)
    local group = Group.getByName(name)
    if not group or not group:isExist() then return nil, 'no group called ' .. name end
    local category = group:getCategory()
    if category ~= Group.Category.AIRPLANE and category ~= Group.Category.HELICOPTER then
        return nil, name .. ' is not an aircraft group'
    end
    local leader = nil
    for _, unit in ipairs(group:getUnits() or {}) do
        if unit:isExist() and unit:getLife() > 0 then leader = unit; break end
    end
    if not leader then return nil, name .. ' has no aircraft left' end
    if not leader:isActive() then return nil, name .. ' has not been started yet' end
    return group, leader
end

local function leader_group(player_name)
    local ok, unit = pcall(Unit.getByName, player_name or '')
    if not ok or not unit or not unit:isExist() or not unit:getGroup() then return nil end
    return unit, unit:getGroup()
end

-- A new order: setTask only. resetTask before it (added 2026-11-04 for a pushed strike that went on) made every
-- order lost (live 2026-11-05: after takeoff it obeyed no wing_fly / Follow / Orbit and went back to land at
-- Kobuleti, the end of its Mission Editor route). Pushed attacks are one attack each (attackQty 1) and end by
-- themselves.
local function order_task(group, task)
    group:getController():setTask(task)
end

-- The agent decides when to go home (user, 2026-11-05): DCS's AI must not turn back by itself at bingo or
-- winchester. Set at birth, engine start and takeoff (an option set while uncontrolled may be dropped, like a task).
local function no_auto_rtb(group)
    return pcall(function()
        local controller = group:getController()
        controller:setOption(AI.Option.Air.id.RTB_ON_BINGO, false)
        controller:setOption(AI.Option.Air.id.RTB_ON_OUT_OF_AMMO, 0)
    end)
end

local function follow_task(id, leader_id, pos)
    return {id = id, params = {groupId = leader_id, pos = pos, lastWptIndexFlag = false,
                               engagementDistMax = id == 'Escort' and 60000 or nil,
                               targetTypes = id == 'Escort' and {'Air'} or nil}}
end

-- The first waypoint's own tasks (the Mission Editor's CAP, options...) plus Follow on the leader: it joins after
-- takeoff.
local function add_follow(point, lead_group)
    if not lead_group then return end
    local follow = {number = 1, auto = false, enabled = true, id = 'Follow',
                    params = follow_task('Follow', lead_group:getID(), copy(FOLLOW_DEFAULT)).params}
    local task = point.task
    if not (task and task.id == 'ComboTask' and task.params and task.params.tasks) then
        point.task = {id = 'ComboTask', params = {tasks = {follow}}}
        return
    end
    local tasks = {}
    for _, t in pairs(task.params.tasks) do tasks[#tasks + 1] = t end
    table.sort(tasks, function(a, b) return (a.number or 0) < (b.number or 0) end)
    follow.number = #tasks + 1
    tasks[#tasks + 1] = follow
    task.params.tasks = tasks
end

-- An AI aircraft with only its departure waypoint starts its engines and stays on the ground (live 2026-11-04: Viper,
-- uncontrolled, one TakeOffParking point): DCS's AI takes off to fly to its next waypoint. A route of the departure
-- point plus a second one, towards the leader when the leader is away from the field, else 20 km out at 2000 m.
local function departure_route(first, lead, kind)
    local k = KIND[kind] or KIND.airplane
    local x, z = first.x + k.next_m, first.y
    if lead then
        local p = lead:getPoint()
        local dx, dz = p.x - first.x, p.z - first.y
        local d = math.sqrt(dx * dx + dz * dz)
        if d > 5000 then x, z = first.x + dx / d * k.next_m, first.y + dz / d * k.next_m end
    end
    return {first, {x = x, y = z, alt = k.next_alt, alt_type = k.next_alt_type, speed = k.next_speed,
                    type = 'Turning Point', action = 'Turning Point'}}
end

-- airplane / helicopter: a group's (Group.Category) or a unit's (its description's category: 1 = helicopter).
local function group_kind(category)
    return category == Group.Category.HELICOPTER and 'helicopter' or 'airplane'
end
local function unit_kind(unit)
    local ok, desc = pcall(function() return unit:getDesc() end)
    return (ok and type(desc) == 'table' and desc.category == 1) and 'helicopter' or 'airplane'
end

-- Formation needs the same kind (user, 2026-11-05: a helicopter cannot follow an airplane): otherwise no Follow on the
-- leader; it can still be sent anywhere by radio.
local function can_follow(kind, lead)
    return lead ~= nil and unit_kind(lead) == kind
end

-- Start the group: mode cold / hot / runway (from where the Mission Editor placed it on an airfield) or air (beside
-- the leader, 1 km behind and 300 m to the right, at the leader's height and speed). It joins the leader after takeoff.
function T.wing_start(name, mode, player_name)
    local country, category, data = mission_aircraft_group(name)
    if not data then return result(false, 'no aircraft group called ' .. name .. ' in the mission') end
    local live = Group.getByName(name)
    local first_unit = live and live:isExist() and live:getUnit(1)
    local kind = group_kind(category)
    local k = KIND[kind]
    -- UNCONTROLLED in the Mission Editor (user, 2026-11-04: late activation hides the aircraft, uncontrolled keeps it
    -- visible on its spot): DCS's own "Start" command (me_action_db.lua ActionId.START) wakes it up as placed; it
    -- joins the leader after takeoff (event handler below). Only "air" re-spawns it.
    if data.uncontrolled and mode ~= 'air' and not T.wings[name] and first_unit and first_unit:isActive() then
        if not START_TYPES[mode] then return result(false, 'unknown start ' .. tostring(mode)) end
        local route = (data.route or {}).points or {}
        local controller = live:getController()
        local mission = nil
        if route[1] and not route[2] then   -- only the departure point: it would never leave the ground
            local lead, lead_group = leader_group(player_name)
            local first = copy(route[1])
            if can_follow(kind, lead) then add_follow(first, lead_group) end
            mission = {id = 'Mission', params = {route = {points = departure_route(first, lead, kind)}}}
            controller:setTask(mission)
        end
        controller:setCommand({id = 'Start', params = {}})
        -- Live 2026-11-04: engines on, never taxied. The route is given again once its engines run (the event
        -- handler), in case DCS drops a task set while the group was still uncontrolled.
        T.wings[name] = {mode = 'uncontrolled', leader = player_name, join = true, events = {}, last = 'started',
                         route_again = mission, kind = kind}
        env.info('DCS-Instructor: wingman ' .. name .. ' started (uncontrolled), ' ..
                 (mission and 'route of 2 points given' or 'its own route'))
        return result(true, name .. ' started on its parking spot (uncontrolled)')
    end
    if first_unit and first_unit:isActive() and (T.wings[name] or not data.uncontrolled) then
        return result(false, name .. ' is already started')
    end
    local points = (data.route or {}).points or {}
    local first = points[1]
    if not first or not data.units or not data.units[1] then return result(false, name .. ' has no route or units') end
    local lead, lead_group = leader_group(player_name)
    local pos = copy(FOLLOW_DEFAULT)
    data.lateActivation, data.uncontrolled, data.start_time, data.groupId = nil, false, 0, nil
    for _, unit in ipairs(data.units) do unit.unitId = nil end
    if mode == 'air' then
        if not lead then return result(false, 'the leader is not in the mission') end
        if not can_follow(kind, lead) then
            return result(false, 'an air start puts you beside the leader in formation: this ' .. kind ..
                                 ' cannot fly with the leader\'s ' .. unit_kind(lead) .. '; start on the ground')
        end
        local p, v = lead:getPoint(), lead:getVelocity()
        local forward = lead:getPosition().x
        local h = math.atan2(forward.z, forward.x)
        local speed = math.max(math.sqrt(v.x * v.x + v.z * v.z), k.min_speed)
        local alt = math.max(p.y, land.getHeight({x = p.x, y = p.z}) + k.above_ground)
        local x = p.x - k.behind * math.cos(h) - k.right * math.sin(h)
        local z = p.z - k.behind * math.sin(h) + k.right * math.cos(h)
        for i, unit in ipairs(data.units) do
            unit.x, unit.y = x - (i - 1) * 100 * math.cos(h), z - (i - 1) * 100 * math.sin(h)
            unit.alt, unit.alt_type, unit.heading, unit.speed = alt, 'BARO', h, speed
            unit.parking, unit.parking_id = nil, nil
        end
        data.x, data.y = x, z
        data.route.points = {{x = x, y = z, alt = alt, alt_type = 'BARO', speed = speed, type = 'Turning Point',
                              action = 'Turning Point',
                              task = {id = 'ComboTask', params = {tasks = {{number = 1, auto = false, enabled = true,
                                      id = 'Follow', params = follow_task('Follow', lead_group:getID(), pos).params}}}}}}
    else
        local start = START_TYPES[mode]
        if not start then return result(false, 'unknown start ' .. tostring(mode)) end
        if not (first.airdromeId or first.helipadId or first.linkUnit) then
            return result(false, name .. ' begins the mission already flying: it can only start in the air')
        end
        first.type, first.action = start[1], start[2]
        if can_follow(kind, lead) then add_follow(first, lead_group) end
        if not points[2] then data.route.points = departure_route(first, lead, kind) end   -- else the editor's route
    end
    local ok, err = pcall(coalition.addGroup, country, category, data)
    if not ok then return result(false, 'DCS refused to start ' .. name .. ': ' .. tostring(err)) end
    T.wings[name] = {mode = mode, leader = player_name, join = mode ~= 'air', events = {}, last = 'started',
                     kind = kind}
    return result(true, name .. ' started (' .. mode .. ')')
end

-- Fly in formation on the leader: distance behind, interval (+ right, - left), elevation (+ above), metres.
function T.wing_follow(name, player_name, distance, interval, elevation)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    local lead, lead_group = leader_group(player_name)
    if not lead_group then return result(false, 'the leader is not in the mission') end
    local kind = group_kind(group:getCategory())
    if not can_follow(kind, lead) then
        return result(false, 'this ' .. kind .. ' cannot fly formation on the leader\'s ' .. unit_kind(lead))
    end
    local pos = {x = -math.abs(tonumber(distance) or 200), y = tonumber(elevation) or 0, z = tonumber(interval) or 200}
    order_task(group, follow_task('Follow', lead_group:getID(), pos))
    if T.wings[name] then T.wings[name].leader = player_name end
    env.info('DCS-Instructor: wingman ' .. name .. ' follows ' .. tostring(player_name) .. ' at ' .. pos.x .. ', ' ..
             pos.y .. ', ' .. pos.z)
    return result(true, 'following the leader')
end

-- Escort: follow and engage enemy aircraft that come for the leader (within 60 km, DCS's default).
function T.wing_escort(name, player_name)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    local _, lead_group = leader_group(player_name)
    if not lead_group then return result(false, 'the leader is not in the mission') end
    order_task(group, follow_task('Escort', lead_group:getID(), copy(FOLLOW_DEFAULT)))
    return result(true, 'covering the leader')
end

-- An attack on a point of the ground: an airplane bombs it (Bombing); a helicopter only strafes it (user, 2026-11-05:
-- the Mi-8's guns fired when its rockets did not; DCS's Strafing task, me_action_db.lua ActionId.STRAFING, "strafe
-- surface point with guns and rockets", no attack type, a length of the run in m: 0 = DCS chooses).
local function is_helicopter(group)
    return group:getCategory() == Group.Category.HELICOPTER
end
local function point_attack(group, params)
    if is_helicopter(group) then
        params.attackType, params.expend, params.length = nil, nil, params.length or 0
        return {id = 'Strafing', params = params}, 'strafing'
    end
    return {id = 'Bombing', params = params}, 'bombing'
end

-- Attack an enemy group (how = all) or one unit of it (how = one: the first alive, then the pilot clears the next);
-- pushed on top of the current task, so it goes back to it (following, or orbiting the zone) when done.
function T.wing_attack(name, target, how)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    local enemy = Group.getByName(target)
    if not enemy or not enemy:isExist() then
        -- A static object (a parked tank, a truck, a building): DCS's AI attacks it by bombing its exact point.
        local ok, static = pcall(StaticObject.getByName, target)
        if not ok or not static or not static:isExist() then return result(false, 'no group called ' .. target) end
        local p = static:getPoint()
        local controller = group:getController()
        controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.OPEN_FIRE)
        local task, verb = point_attack(group, {point = {x = p.x, y = p.z}, groupAttack = false,
                                                attackQtyLimit = true, attackQty = 1, expend = 'Two'})
        controller:pushTask(task)
        return result(true, verb .. ' the ' .. static:getTypeName() .. ' ' .. target)
    end
    local controller = group:getController()
    controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.OPEN_FIRE)
    -- A helicopter strafes a ground group on its first living unit (one at a time: the zone job gives the next).
    local okc, enemy_category = pcall(function() return enemy:getCategory() end)
    if is_helicopter(group) and okc and (enemy_category == Group.Category.GROUND or
                                         enemy_category == Group.Category.SHIP) then
        for _, unit in ipairs(enemy:getUnits() or {}) do
            local got, alive = pcall(function() return unit:isExist() and unit:getLife() > 0 end)
            if got and alive then
                local p = unit:getPoint()
                controller:pushTask((point_attack(group, {point = {x = p.x, y = p.z}, groupAttack = false,
                                                          attackQtyLimit = true, attackQty = 1})))
                return result(true, 'strafing the ' .. unit:getTypeName() .. ' of ' .. target)
            end
        end
        return result(false, target .. ' has no units left')
    end
    -- A SAM site: DCS's AI attacks it as a group only with anti-radiation missiles (live 2026-11-04: an F-5 with Mk-82
    -- and rockets released nothing on an Osa for 3 minutes); its point it bombs, like a static's.
    for _, unit in ipairs(enemy:getUnits() or {}) do
        local alive, sam = pcall(function()
            return unit:isExist() and unit:getLife() > 0 and unit:hasAttribute('SAM elements')
        end)
        if alive and sam then
            local p = unit:getPoint()
            local task, verb = point_attack(group, {point = {x = p.x, y = p.z}, groupAttack = false,
                                                    attackQtyLimit = true, attackQty = 1, expend = 'Two'})
            controller:pushTask(task)
            return result(true, verb .. ' the point of the ' .. unit:getTypeName() .. ' of ' .. target ..
                                ' (a SAM: DCS attacks it as a group only with anti-radiation missiles)')
        end
    end
    if how == 'one' then
        for _, unit in ipairs(enemy:getUnits() or {}) do
            if unit:isExist() and unit:getLife() > 0 then
                controller:pushTask({id = 'AttackUnit', params = {unitId = unit:getID(), groupAttack = false,
                                                                  attackQtyLimit = false, attackQty = 1}})
                return result(true, 'attacking one ' .. unit:getTypeName() .. ' of ' .. target)
            end
        end
        return result(false, target .. ' has no units left')
    end
    controller:pushTask({id = 'AttackGroup', params = {groupId = enemy:getID(), groupAttack = false,
                                                       attackQtyLimit = false, attackQty = 1}})
    return result(true, 'attacking ' .. target)
end

-- Attack whatever it finds in a zone, pass after pass, while orbiting over it (DCS's en-route EngageTargetsInZone
-- with an Orbit, as a waypoint's ComboTask in the Mission Editor): ground = ground units and ships, air = aircraft.
local ZONE_TARGETS = {ground = {'Ground Units', 'Ships'}, air = {'Air'}}
function T.wing_engage_zone(name, lat, lon, radius, against, alt, speed)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    if not ZONE_TARGETS[against] then return result(false, 'unknown targets ' .. tostring(against)) end
    local controller = group:getController()
    controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.OPEN_FIRE)
    local point = point_of(lat, lon)
    controller:setTask({id = 'ComboTask', params = {tasks = {
        {number = 1, auto = false, enabled = true, id = 'EngageTargetsInZone',
         params = {point = point, zoneRadius = tonumber(radius) or 3000, targetTypes = ZONE_TARGETS[against],
                   priority = 0}},
        {number = 2, auto = false, enabled = true, id = 'Orbit',
         params = {pattern = 'Circle', point = point, altitude = tonumber(alt), speed = tonumber(speed)}},
    }}})
    return result(true, 'engaging ' .. against .. ' targets in the zone')
end

-- Strike a point on the ground (pushed: back to the task before afterwards). DCS's AI takes a point whatever is
-- there (user's idea, 2026-11-04: an aircraft without anti-radiation missiles may not "attack" a SAM group, it may
-- bomb its point). weapon: auto / bombs / rockets / guns (DCS's weapon flags, as in MOOSE's ENUMS.WeaponFlag);
-- alt: attack altitude in m (0 = DCS chooses); heading: attack direction in degrees (-1 = DCS chooses); attack: auto /
-- dive / level.
local WEAPON_FLAGS = {auto = 3221225470, bombs = 2147485680, rockets = 30720, guns = 805306368}
function T.wing_bomb(name, lat, lon, weapon, alt, heading, attack)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    -- One attack, then back to the task before (live 2026-11-04: with no limit it kept attacking the point, and
    -- "return to base" or another attack seemed to leave it orbiting).
    local params = {point = point_of(lat, lon), groupAttack = false, attackQtyLimit = true, attackQty = 1,
                    expend = 'Auto', weaponType = WEAPON_FLAGS[weapon or 'auto'] or WEAPON_FLAGS.auto}
    alt, heading = tonumber(alt) or 0, tonumber(heading) or -1
    if alt > 0 then params.altitude, params.altitudeEnabled = alt, true end
    if heading >= 0 then params.direction, params.directionEnabled = math.rad(heading), true end
    if attack == 'dive' then params.attackType = 'Dive' end
    local controller = group:getController()
    controller:setOption(AI.Option.Air.id.ROE, AI.Option.Air.val.ROE.OPEN_FIRE)
    local task, verb = point_attack(group, params)
    controller:pushTask(task)
    return result(true, (verb == 'strafing' and 'strafing' or 'striking') .. ' the point with ' .. (weapon or 'auto') ..
                        (alt > 0 and (' from ' .. math.floor(alt) .. ' m') or '') ..
                        (heading >= 0 and (', heading ' .. math.floor(heading)) or ''))
end

-- Fly to a point at an altitude (m) above the sea (BARO) or the ground (RADIO), at a speed (m/s), and orbit there
-- at that altitude: the agent flies the aircraft like a remote control, within what DCS's AI allows.
function T.wing_fly(name, lat, lon, alt, alt_type, speed)
    local group, leader = air_group(name)
    if not group then return result(false, leader) end
    local from, to = leader:getPoint(), point_of(lat, lon)
    alt, speed = tonumber(alt) or 1000, tonumber(speed) or 200
    local kind = alt_type == 'RADIO' and 'RADIO' or 'BARO'
    local orbit_alt = kind == 'RADIO' and (land.getHeight({x = to.x, y = to.y}) + alt) or alt
    order_task(group, {id = 'Mission', params = {route = {points = {
        {x = from.x, y = from.z, alt = alt, alt_type = kind, speed = speed, type = 'Turning Point',
         action = 'Turning Point'},
        {x = to.x, y = to.y, alt = alt, alt_type = kind, speed = speed, type = 'Turning Point',
         action = 'Turning Point', task = {id = 'ComboTask', params = {tasks = {
             {number = 1, auto = false, enabled = true, id = 'Orbit',
              params = {pattern = 'Circle', point = to, altitude = orbit_alt, speed = speed}}}}}},
    }}}})
    return result(true, 'flying there at ' .. math.floor(alt) .. ' m ' ..
                        (kind == 'RADIO' and 'above the ground' or 'above the sea'))
end

-- Orbit over a point at an altitude (m) and speed (m/s).
function T.wing_orbit(name, lat, lon, alt, speed)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    order_task(group, {id = 'Orbit', params = {pattern = 'Circle', point = point_of(lat, lon),
                                               altitude = tonumber(alt), speed = tonumber(speed)}})
    return result(true, 'orbiting')
end

-- Return to an airfield and land there.
function T.wing_land(name, airbase_name)
    local group, leader = air_group(name)
    if not group then return result(false, leader) end
    local base = Airbase.getByName(airbase_name)
    if not base then return result(false, 'no airfield called ' .. airbase_name) end
    local from, to, v = leader:getPoint(), base:getPoint(), leader:getVelocity()
    local speed = math.max(math.sqrt(v.x * v.x + v.z * v.z), KIND[group_kind(group:getCategory())].min_speed)
    local landing = {x = to.x, y = to.z, alt = to.y, alt_type = 'BARO', speed = speed, type = 'Land',
                     action = 'Landing'}
    -- A FARP (helipad) for a helicopter, else an airfield.
    local okd, desc = pcall(function() return base:getDesc() end)
    if okd and type(desc) == 'table' and desc.category == Airbase.Category.HELIPAD then
        landing.helipadId = base:getID()
    else
        landing.airdromeId = base:getID()
    end
    order_task(group, {id = 'Mission', params = {route = {points = {
        {x = from.x, y = from.z, alt = from.y, alt_type = 'BARO', speed = speed, type = 'Turning Point',
         action = 'Turning Point'},
        landing,
    }}}})
    return result(true, 'returning to ' .. airbase_name)
end

-- A helicopter lands at a point (user, 2026-11-05: "land here", "land beside Tango", at an F10 map mark): DCS's own
-- Land task (me_action_db.lua ActionId.LAND, "land on the ground temporary"); DCS picks a spot it can land on near
-- the point. seconds 0 = it stays until the next order; combat = DCS's combat landing.
function T.wing_land_at(name, lat, lon, seconds, how)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    if group:getCategory() ~= Group.Category.HELICOPTER then
        return result(false, 'only a helicopter lands at a point: an airplane needs an airfield')
    end
    seconds = tonumber(seconds) or 0
    order_task(group, {id = 'Land', params = {point = point_of(lat, lon), durationFlag = seconds > 0,
                                              duration = seconds > 0 and seconds or nil,
                                              combatLandingFlag = how == 'combat'}})
    env.info('DCS-Instructor: wingman ' .. name .. ' lands at ' .. tostring(lat) .. ', ' .. tostring(lon))
    return result(true, 'landing there' .. (seconds > 0 and (' for ' .. math.floor(seconds / 60 + 0.5) .. ' min')
                                            or ' until the next order'))
end

-- A helicopter carries a squad (user, 2026-11-05: "Cobra, pick up Tango at the extraction point", "take Tango to
-- Z1"): DCS's own transport tasks (me_action_db.lua ActionId.EMBARKING / DISEMBARKING; the squad waits with
-- EmbarkToTransport, T.wait_pickup). DCS lands it at the point; the troops walk in, or out. Only aircraft with a cabin
-- (InternalCargo: Mi-8, UH-1...). squad: the ground group's name.
local function transport_task(name, lat, lon, squad, id)
    local group, problem = air_group(name)
    if not group then return nil, problem end
    if not is_helicopter(group) then return nil, 'only a helicopter carries troops here' end
    local troops = Group.getByName(squad or '')
    if not troops or not troops:isExist() then return nil, 'no group called ' .. tostring(squad) end
    local to = point_of(lat, lon)
    local params = {x = to.x, y = to.y, groupsForEmbarking = {troops:getID()}}
    if id == 'Embarking' then params.durationFlag, params.distributionFlag, params.distribution = false, false, {} end
    order_task(group, {id = id, params = params})
    env.info('DCS-Instructor: wingman ' .. name .. ' ' .. id .. ' ' .. squad .. ' at ' .. tostring(lat) .. ', ' ..
             tostring(lon))
    return group
end

function T.wing_pickup(name, lat, lon, squad)
    local group, problem = transport_task(name, lat, lon, squad, 'Embarking')
    if not group then return result(false, problem) end
    return result(true, 'going to land there and pick up ' .. squad)
end

function T.wing_dropoff(name, lat, lon, squad)
    local group, problem = transport_task(name, lat, lon, squad, 'Disembarking')
    if not group then return result(false, problem) end
    return result(true, 'going to land there and drop ' .. squad)
end

-- Rules of engagement in the air: free (weapons free), return (return fire), hold (weapons hold).
function T.wing_roe(name, value)
    local group, problem = air_group(name)
    if not group then return result(false, problem) end
    local values = {free = AI.Option.Air.val.ROE.WEAPON_FREE, ['return'] = AI.Option.Air.val.ROE.RETURN_FIRE,
                    hold = AI.Option.Air.val.ROE.WEAPON_HOLD}
    if not values[value] then return result(false, 'unknown rules of engagement ' .. tostring(value)) end
    group:getController():setOption(AI.Option.Air.id.ROE, values[value])
    return result(true, 'rules of engagement ' .. value)
end

-- What happened to each started wingman, from DCS's own events: model time of each, and the last one. The combat
-- ones (it fired a weapon, killed something, was hit, hit something) also go to its combat log, numbered, for the
-- agent's callouts ("Fox 2", "splash one", "I'm hit").
local WING_EVENTS = {S_EVENT_BIRTH = 'birth', S_EVENT_ENGINE_STARTUP = 'engine_startup', S_EVENT_TAKEOFF = 'takeoff',
                     S_EVENT_LAND = 'land', S_EVENT_ENGINE_SHUTDOWN = 'engine_shutdown', S_EVENT_CRASH = 'crash',
                     S_EVENT_EJECTION = 'ejection', S_EVENT_DEAD = 'dead', S_EVENT_PILOT_DEAD = 'pilot_dead',
                     S_EVENT_HIT = 'hit', S_EVENT_SHOT = 'shot', S_EVENT_KILL = 'kill'}
local COMBAT_LOG_KEPT = 20
local wing_event_names = {}
for key, label in pairs(WING_EVENTS) do
    if (world.event or {})[key] then wing_event_names[world.event[key]] = label end
end
if DCSInstructorWingEvents and world.removeEventHandler then   -- a reloaded script: the old handler goes
    world.removeEventHandler(DCSInstructorWingEvents)
end

local function wing_of(object)
    if not object then return nil end
    local ok, group_name = pcall(function() return object:getGroup():getName() end)
    if ok and T.wings[group_name] then return T.wings[group_name], group_name end
    return nil
end

local function type_of(object)
    local ok, name = pcall(function() return object:getTypeName() end)
    return ok and name or nil
end

local function weapon_name(weapon)
    local ok, desc = pcall(function() return weapon:getDesc() end)
    if ok and desc then return desc.displayName or desc.typeName end
    return type_of(weapon)
end

local function combat(wing, entry)
    wing.combat_n = (wing.combat_n or 0) + 1
    entry.n, entry.t = wing.combat_n, timer.getTime()
    wing.combat = wing.combat or {}
    wing.combat[#wing.combat + 1] = entry
    while #wing.combat > COMBAT_LOG_KEPT do table.remove(wing.combat, 1) end
end

DCSInstructorWingEvents = {onEvent = function(_, event)
    local label = wing_event_names[event.id]
    if not label then return end
    if label == 'hit' then   -- the wingman hit, or was hit by, something
        local target_wing = wing_of(event.target)
        if target_wing then
            target_wing.events.hit = timer.getTime()
            combat(target_wing, {kind = 'hit_taken', weapon = event.weapon and weapon_name(event.weapon),
                                 by = event.initiator and type_of(event.initiator)})
        end
        local own = wing_of(event.initiator)
        if own and own ~= target_wing then
            combat(own, {kind = 'hit_target', target = event.target and type_of(event.target)})
        end
        return
    end
    local wing, group_name = wing_of(event.initiator)
    if not wing then return end
    if label == 'shot' then
        combat(wing, {kind = 'weapon_released', weapon = event.weapon and weapon_name(event.weapon)})
        return
    end
    if label == 'kill' then
        local victim = event.target
        -- Scenery hit by the bombs (live 2026-11-04: "you destroyed a SMALL_CRASH" five times, a BAK_NEW_CRUSH):
        -- no target, no call.
        local okc, object_category = pcall(function() return Object.getCategory(victim) end)
        if okc and object_category == Object.Category.SCENERY then return end
        local ok, desc = pcall(function() return victim:getDesc() end)
        local okn, victim_name = pcall(function() return victim:getName() end)
        combat(wing, {kind = 'kill', target = victim and type_of(victim), name = okn and victim_name or nil,
                      air = ok and desc and (desc.category == 0 or desc.category == 1) or false})
        return
    end
    wing.events[label] = timer.getTime()
    wing.last = label
    env.info('DCS-Instructor: wingman ' .. group_name .. ' ' .. label)
    if label == 'birth' or label == 'engine_startup' or label == 'takeoff' then
        local got, group = pcall(function() return event.initiator:getGroup() end)
        local done, problem = false, 'no group'
        if got and group then done, problem = no_auto_rtb(group) end
        if not done then env.info('DCS-Instructor: wingman ' .. group_name .. ' no auto RTB: ' .. tostring(problem)) end
    end
    if label == 'engine_startup' and wing.route_again then
        local mission = wing.route_again
        wing.route_again = nil
        local done, problem = pcall(function() event.initiator:getGroup():getController():setTask(mission) end)
        env.info('DCS-Instructor: wingman ' .. group_name .. ' route given again: ' ..
                 (done and 'ok' or tostring(problem)))
    end
    if label == 'takeoff' and wing.join then   -- airborne: join the leader (an uncontrolled one flies its own route)
        wing.join = false
        local lead, lead_group = leader_group(wing.leader)
        local done, problem = lead_group ~= nil, 'the leader is not in the mission'
        -- Live 2026-11-04: airborne before the leader, Follow on a leader still on the ground made it fly off and
        -- come back round to land. The program (wingman/desk.py keep_formation) holds it over the field instead and
        -- joins it once the leader is airborne.
        local ok_air, lead_in_air = pcall(function() return lead:inAir() end)
        if lead_group and ok_air and not lead_in_air then
            lead_group, done, problem = nil, false, 'the leader is still on the ground: the program holds it'
        end
        local kind = wing.kind or unit_kind(event.initiator)
        if lead_group and not can_follow(kind, lead) then
            lead_group, done, problem = nil, false, 'this ' .. kind .. ' does not fly formation on the leader\'s ' ..
                unit_kind(lead) .. ': the program holds it'
        end
        if lead_group then
            done, problem = pcall(function()
                event.initiator:getGroup():getController():setTask(follow_task('Follow', lead_group:getID(),
                                                                               copy(FOLLOW_DEFAULT)))
            end)
        end
        env.info('DCS-Instructor: wingman ' .. group_name .. ' joins the leader: ' ..
                 (done and 'ok' or tostring(problem)))
    end
end}
if world.addEventHandler then world.addEventHandler(DCSInstructorWingEvents) end

-- An aircraft group's details for its wingman agent: started or not, airborne, speed, heading, fuel, damage,
-- weapons, what its sensors detect, the radio path to the leader, and DCS's events since it was started.
local function wing_details(group, leader, player_name)
    local p, v = leader:getPoint(), leader:getVelocity()
    local forward = leader:getPosition().x
    local weapons = {}
    for _, ammo in ipairs(leader:getAmmo() or {}) do
        local desc = ammo.desc or {}
        local kind = WEAPON_CATEGORY[desc.category] or 'other'
        -- A missile is air-to-air (missileCategory 1, AAM) or for the ground (anti-radiation, anti-ship, other).
        local against = kind == 'gun' and 'both' or (kind == 'missile' and desc.missileCategory == 1) and 'air'
            or 'ground'
        weapons[#weapons + 1] = {name = desc.displayName or desc.typeName or '?', count = ammo.count or 0,
                                 kind = kind, against = against}
    end
    -- The group's main task in the Mission Editor (CAP, CAS, Ground Attack...): DCS's AI only does what it allows.
    T.editor_tasks = T.editor_tasks or {}
    if T.editor_tasks[group:getName()] == nil then
        local _, _, data = mission_aircraft_group(group:getName())
        T.editor_tasks[group:getName()] = data and data.task or false
    end
    local air = {active = leader:isActive(), in_air = leader:inAir(), type = leader:getTypeName(),
                 life = leader:getLife(), life0 = leader:getLife0(), fuel = leader:getFuel(), vx = v.x, vz = v.z,
                 heading_deg = math.deg(math.atan2(forward.z, forward.x)) % 360,
                 ground_m = land.getHeight({x = p.x, y = p.z}), weapons = weapons,
                 editor_task = T.editor_tasks[group:getName()] or nil}
    air.kind = group_kind(group:getCategory())
    -- The gear (draw argument 0, the nose gear: 0 up, 1 down): down in the air with no landing ordered = DCS's AI
    -- is landing it by itself (wingman/desk.py watch_orders). Airplanes only: many helicopters have a fixed gear
    -- (user, 2026-11-05: the Mi-8); theirs is seen from height and speed in the desk.
    if air.kind == 'airplane' then
        local got_gear, gear = pcall(function() return leader:getDrawArgumentValue(0) end)
        if got_gear and type(gear) == 'number' then air.gear_down = gear > 0.5 end
    end
    local wing = T.wings[group:getName()]
    if not wing then   -- visible on its spot but asleep until "Start" (UNCONTROLLED in the Mission Editor)?
        T.uncontrolled = T.uncontrolled or {}
        if T.uncontrolled[group:getName()] == nil then
            local _, _, data = mission_aircraft_group(group:getName())
            T.uncontrolled[group:getName()] = (data and data.uncontrolled) == true
        end
        air.uncontrolled = T.uncontrolled[group:getName()]
    end
    if wing then
        air.started, air.last_event, air.events = wing.mode, wing.last, {}
        local now = timer.getTime()
        for label, at in pairs(wing.events) do air.events[label] = now - at end   -- seconds ago
        air.combat = {}
        for _, entry in ipairs(wing.combat or {}) do
            local item = copy(entry)
            item.t, item.s_ago = nil, now - entry.t
            air.combat[#air.combat + 1] = item
        end
    end
    local lead = leader_group(player_name)
    if lead then
        air.leader_kind = unit_kind(lead)
        local me = lead:getPoint()
        air.radio = {line_of_sight = land.isVisible(p, me), distance_m = math.sqrt((p.x - me.x) ^ 2 + (p.z - me.z) ^ 2)}
    end
    return air
end

-- Mission flags the agents listen to (newline separated names, e.g. "10\nconvoy_left"), as JSON {name = value}.
-- Flags are the mission maker's side of the agents' Triggers: "unit in zone -> FLAG 10 ON" calls an agent.
function T.flags(names)
    local values = {}
    for name in string.gmatch(names or '', '[^\n]+') do
        values[name] = trigger.misc.getUserFlag(name) or 0
    end
    return encode(values)
end

-- Set one flag (the hook only lets through the flags an agent's actions list): the mission's own triggers react.
function T.set_flag(name, value)
    trigger.action.setUserFlag(name, tonumber(value) or 1)
    return result(true, 'flag ' .. name .. ' = ' .. tostring(tonumber(value) or 1))
end

-- Radio line of sight between a squad and the pilot's aircraft, terrain included (land.isVisible, as the airfield
-- radars in dcs/instructor_radar.lua): infantry carry a manpack radio (antenna about 2 m up), any other ground unit
-- is a vehicle (antenna about 3 m up, more power); the aircraft where it is (+1 m on the ground).
-- A ship's antennas are on its mast (user, 2026-11-07: "what range would a ship's radio have in real life"): UHF/VHF
-- go by line of sight, so the height is what gives a ship its range.
local ANTENNA_M = {manpack = 2, vehicle = 3, ship = 25}
local function radio_path(leader, player_name)
    if not player_name or player_name == '' then return nil end
    local ok, unit = pcall(Unit.getByName, player_name)
    if not ok or not unit or not unit:isExist() then return nil end
    local infantry, is_infantry = pcall(function() return leader:hasAttribute('Infantry') end)
    local got_ship, ship = pcall(function() return leader:getGroup():getCategory() == Group.Category.SHIP end)
    local kind = (got_ship and ship) and 'ship' or (infantry and is_infantry) and 'manpack' or 'vehicle'
    local a, b = leader:getPoint(), unit:getPoint()
    local visible = land.isVisible({x = a.x, y = a.y + ANTENNA_M[kind], z = a.z}, {x = b.x, y = b.y + 1, z = b.z})
    return {line_of_sight = visible, distance_m = math.sqrt((a.x - b.x) ^ 2 + (a.z - b.z) ^ 2), kind = kind}
end

-- What a ground group is made of (user, 2026-11-05: armour as well as infantry; some vehicles carry troops): its
-- living units by type, how many are infantry and how many vehicles, and what DCS itself says of their troop
-- compartments: getDescentCapacity / getDescentOnBoard, which DCS's radio menu for a ground unit shows
-- (RadioCommandDialogPanel/Config/groundUnit.lua). Not documented for the mission scripting environment, so tried in
-- a pcall and sent only when it answers; the program also knows the capacities from DCS's unit files
-- (shared/cargo.py). life / life0: the living units' life summed, now and new (user, 2026-11-05: a squad or a vehicle
-- group says when it takes hits, not only when it loses men).
local function ground_details(group)
    local out, capacity, on_board, probed = {types = {}, infantry = 0, vehicles = 0, life = 0, life0 = 0}, 0, 0, false
    for _, unit in ipairs(group:getUnits() or {}) do
        if unit:isExist() and unit:getLife() > 0 then
            out.life = out.life + unit:getLife()
            local got_life0, life0 = pcall(function() return unit:getLife0() end)
            out.life0 = out.life0 + ((got_life0 and tonumber(life0)) or unit:getLife())
            local kind = unit:getTypeName()
            out.types[kind] = (out.types[kind] or 0) + 1
            if is_infantry(unit) then out.infantry = out.infantry + 1 else out.vehicles = out.vehicles + 1 end
            local got, seats = pcall(function() return unit:getDescentCapacity() end)
            if got and tonumber(seats) then probed, capacity = true, capacity + tonumber(seats) end
            local got_on, count = pcall(function() return unit:getDescentOnBoard() end)
            if got_on and tonumber(count) then on_board = on_board + tonumber(count) end
            -- Rounds left (artillery: a drop means it fired), summed over the living units, by weapon name.
            local got_ammo, ammo = pcall(function() return unit:getAmmo() end)
            for _, item in ipairs(got_ammo and ammo or {}) do
                local desc = item.desc or {}
                local weapon = desc.displayName or desc.typeName or '?'
                out.ammo = out.ammo or {}
                out.ammo[weapon] = (out.ammo[weapon] or 0) + (item.count or 0)
            end
        end
    end
    if probed then out.descent_capacity, out.descent_on_board = capacity, on_board end
    return out
end

-- What the agents' groups are doing, and the mission's trigger zones (names and centres), as JSON. player_name: the
-- pilot's unit, for each group's "radio" {line_of_sight, distance_m} to it, and "flight": the player's wingmen.
-- A controller's sensor (controller/desk.py: an AWACS, an EWR, any radar group): only where it is, alive, and what its
-- radar sees (DCSInstructorRadar.own_contacts) and its receivers hear (emitters); nothing else is done with it (an
-- AI aircraft is never adopted as a wingman here). Asked as "sensor:<group>" in the watched list.
local SENSOR_PREFIX = 'sensor:'
local SENSOR_CONTACTS = 40
local function sensor_status(group, player_name)
    local alive, leader = 0, nil
    for _, unit in ipairs(group:getUnits() or {}) do
        if unit:isExist() and unit:getLife() > 0 then alive = alive + 1; leader = leader or unit end
    end
    local entry = {exists = true, alive = alive, size = group:getInitialSize(), sensor = {}}
    if not leader then return entry end
    local p, v = leader:getPoint(), leader:getVelocity()
    entry.lat, entry.lon = coord.LOtoLL(p)
    entry.alt_m, entry.speed_ms = p.y, math.sqrt(v.x * v.x + v.z * v.z)
    local got_air, in_air = pcall(function() return leader:inAir() end)
    entry.sensor.type, entry.sensor.in_air = leader:getTypeName(), got_air and in_air or false
    if DCSInstructorRadar and DCSInstructorRadar.own_contacts then
        local got_radar, radar = pcall(DCSInstructorRadar.own_contacts, leader, player_name, SENSOR_CONTACTS)
        if got_radar then entry.sensor.radar = radar else entry.sensor.radar_error = tostring(radar) end
        local got_esm, heard = pcall(DCSInstructorRadar.emitters, leader)
        if got_esm then entry.sensor.emitters = heard else entry.sensor.esm_error = tostring(heard) end
    end
    return entry
end

function T.status(names, player_name)
    local groups = {}
    for line in string.gmatch(names or '', '[^\n]+') do
        local name, is_sensor = line, false
        if line:sub(1, #SENSOR_PREFIX) == SENSOR_PREFIX then name, is_sensor = line:sub(#SENSOR_PREFIX + 1), true end
        local group = Group.getByName(name)
        local entry = {exists = false}
        if is_sensor then
            if group and group:isExist() then
                local ok, found = pcall(sensor_status, group, player_name)
                entry = ok and found or {exists = true, sensor_error = tostring(found)}
            end
            groups[SENSOR_PREFIX .. name] = entry
        elseif group and group:isExist() then
            local units, alive = group:getUnits() or {}, 0
            local leader = nil
            for _, unit in ipairs(units) do
                if unit:isExist() and unit:getLife() > 0 then alive = alive + 1; leader = leader or unit end
            end
            entry = {exists = true, alive = alive, size = group:getInitialSize()}
            if leader then
                local p, v = leader:getPoint(), leader:getVelocity()
                entry.lat, entry.lon = coord.LOtoLL(p)
                entry.alt_m, entry.speed_ms = p.y, math.sqrt(v.x * v.x + v.z * v.z)
                local ok, enemies = pcall(enemies_of, group)
                enemies = ok and enemies or {}
                if group:getCategory() == Group.Category.GROUND or group:getCategory() == Group.Category.SHIP then
                    local ship = group:getCategory() == Group.Category.SHIP
                    local oks, statics = pcall(statics_seen_by, leader,
                                               ship and BRIDGE_EYES_M or is_infantry(leader) and SOLDIER_EYES_M
                                                   or CREW_EYES_M, ship and SHIP_STATIC_SEEN_M or nil)
                    for _, static in ipairs(oks and statics or {}) do enemies[#enemies + 1] = static end
                end
                if #enemies > 0 then entry.enemies = enemies end
                local category = group:getCategory()
                if category == Group.Category.AIRPLANE or category == Group.Category.HELICOPTER then
                    local got, air = pcall(wing_details, group, leader, player_name)
                    if got then entry.air = air else entry.air_error = tostring(air) end
                    -- Started by the mission itself (live 2026-11-04: Viper set to a hot start, neither late
                    -- activation nor uncontrolled): adopted as a wingman, so DCS's events and its combat log reach the
                    -- agent; it joins the leader on its takeoff (not when it is already flying).
                    if got and air.active and air.uncontrolled == false and not T.wings[name] then
                        T.wings[name] = {mode = 'mission', leader = player_name, join = not air.in_air, events = {},
                                         last = nil}
                        env.info('DCS-Instructor: wingman ' .. name .. ' adopted (started by the mission)')
                        no_auto_rtb(group)
                        air.started = 'mission'
                    end
                    -- Its own eyes too, only once it flies (started: active and not asleep on its spot).
                    if got and air.active and (air.started or not air.uncontrolled) then
                        local seen, seen_enemies, friends = pcall(eyes_of, leader)
                        if seen then
                            local merged = merge_enemies(ok and enemies or {}, seen_enemies)
                            entry.enemies = #merged > 0 and merged or nil
                            if #friends > 0 then air.friends_in_sight = friends end
                        else
                            entry.eyes_error = tostring(seen_enemies)
                        end
                    end
                else
                    local got, radio = pcall(radio_path, leader, player_name)
                    if got and radio then entry.radio = radio end
                    if category == Group.Category.GROUND or category == Group.Category.SHIP then
                        local got_ground, ground = pcall(ground_details, group)
                        if got_ground then entry.ground = ground else entry.ground_error = tostring(ground) end
                    end
                    if category == Group.Category.SHIP then
                        local got_ship, ship = pcall(ship_details, leader)
                        if got_ship then entry.ship = ship else entry.ship_error = tostring(ship) end
                        -- Its radar picture, friend and foe (dcs/instructor_radar.lua, the airfield radars' model).
                        if got_ship and DCSInstructorRadar and DCSInstructorRadar.own_contacts then
                            local got_radar, radar = pcall(DCSInstructorRadar.own_contacts, leader, player_name)
                            if got_radar then ship.radar = radar else entry.radar_error = tostring(radar) end
                        end
                    end
                end
            end
        end
        if not is_sensor then groups[name] = entry end
    end
    local zones = {}
    local ok, list = pcall(function() return env.mission.triggers.zones end)
    for _, zone in ipairs(ok and list or {}) do
        local lat, lon = coord.LOtoLL({x = zone.x, y = 0, z = zone.y})
        zones[#zones + 1] = {name = zone.name, lat = lat, lon = lon, radius_m = zone.radius}
    end
    -- The pilot's F10 map marks (user, 2026-11-05: "Cobra, land at LZ1"): a mark with a text is a place by that text
    -- (its first line), like a zone; only the marks the pilot's side can see.
    local got_side, side = pcall(function() return Unit.getByName(player_name or ''):getCoalition() end)
    side = got_side and side or nil
    local got_marks, marks = pcall(function() return world.getMarkPanels() end)
    for _, mark in ipairs(got_marks and type(marks) == 'table' and marks or {}) do
        local text = type(mark.text) == 'string' and mark.text:match('[^\r\n]+')
        text = text and text:match('^%s*(.-)%s*$')
        if text and text ~= '' and mark.pos and (side == nil or mark.coalition == -1 or mark.coalition == side) then
            local lat, lon = coord.LOtoLL(mark.pos)
            zones[#zones + 1] = {name = text:sub(1, 40), lat = lat, lon = lon, radius_m = 0, mark = true}
        end
    end
    local out = {groups = groups, zones = zones}
    local got_flight, flight = pcall(flight_of, player_name)
    if got_flight and flight and #flight > 0 then
        out.flight = flight
        local got_radio, radio = pcall(flight_radio, player_name)
        if got_radio and radio then out.flight_radio = radio end
    end
    return encode(out)
end
