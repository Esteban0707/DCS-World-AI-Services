-- DCS-Instructor airfield radars. Runs inside DCS's mission scripting environment, sent there by
-- dcs/instructor_hook.lua (net.dostring_in), which prepends the JSON encoder as DCSInstructorRadar_encode.
--
-- A radar belongs to an airfield by its name: a ground unit or ship with a radar sensor whose unit name or group
-- name starts with the airfield's name, spaces/dashes/case ignored ("Senaki-Kolkhi", "Senaki-Kolkhi-2",
-- "Senaki-Kolkhi EWR"). Every radar of that airfield is part of its network; no mission trigger is needed.
--
-- Detection is our own radar model, not the AI's target list: Controller:getDetectedTargets() reports enemies only,
-- so a friendly airfield radar never listed the player (seen live 2026-11-01). An aircraft is a contact of a radar
-- when it is airborne, in line of sight from the antenna (land.isVisible: terrain masks it) and within the distance
-- at which that radar detects that aircraft. Any coalition. The distance comes from DCS's own data (2026-11-02):
-- - the radar's air detection distances (Unit:getSensors()): upper or lower hemisphere (target above or below the
--   antenna) and head-on or tail-on (target flying towards or away from the radar);
-- - scaled by the target's radar cross section with the radar equation: distance ~ (RCS / REFERENCE_RCS_M2)^(1/4).
--   The RCS comes from DCS at run time (Unit:getDesc().RCS), else from DCSInstructorRadar_RCS (dcs/aircraft_rcs.lua,
--   built by tools/build_rcs.py from the aircraft definitions), else DEFAULT_RCS_M2; each contact says which.
-- REFERENCE_RCS_M2, the target size DCS's detection distances are given for, is not in DCS's plain-text files: it is
-- an assumption to calibrate. For that, each network also reports what the radars' own AI detects of enemy aircraft
-- (Controller:getDetectedTargets works for enemies) next to what this model says ("calibration").
--
-- DCSInstructorRadar.scan(player_unit_name) returns JSON: {airfield name: {radars: [names], range_km: {name: km},
-- contacts: [...], calibration: [...]}}, each contact: unit, type, callsign, player (true for the pilot), lat, lon,
-- alt_m, vx/vy/vz (m/s, DCS axes: x north, z east, y up), rcs_m2, rcs_source (dcs/table/default), range_km (how far
-- this radar sees that aircraft now), dist_km (from that radar); each calibration entry: radar, unit, type, rcs_m2, dist_km, model_km, ai.
DCSInstructorRadar = DCSInstructorRadar or {}
local R = DCSInstructorRadar
R.radars, R.found_at = nil, nil   -- a reload (new version of this script) finds the radars again
local REFIND_S = 30            -- radars are looked up again this often (units can spawn or die)
local ANTENNA_HEIGHT_M = 10    -- line of sight is taken from this height above the radar unit
local DEFAULT_RANGE_M = 100000 -- when a radar's sensor data gives no air detection distance
local REFERENCE_RCS_M2 = 5     -- assumption (see above): the target size of DCS's detection distances
local DEFAULT_RCS_M2 = 5       -- an aircraft whose RCS is known neither to DCS's API nor to the table
local CALIBRATION_FACTOR = 2   -- enemy aircraft up to this many times the radar's range are reported for calibration

local function norm(text)
    return (tostring(text or ''):lower():gsub('[^%w]', ''))
end

-- The airfield a name belongs to: the longest airfield name it starts with.
local function owner(name, bases)
    local best
    for _, base in ipairs(bases) do
        if base.key ~= '' and name:sub(1, #base.key) == base.key and (not best or #base.key > #best.key) then
            best = base
        end
    end
    return best
end

-- A unit's radar air detection distances: {upper = {headOn, tailOn}, lower = {...}}, the largest of each found in
-- its radar sensors (a unit may list several), and the overall largest ("range", shown in the GUI).
local function radar_data(unit)
    local ok, sensors = pcall(unit.getSensors, unit)
    local data = {upper = {headOn = 0, tailOn = 0}, lower = {headOn = 0, tailOn = 0}, range = 0}
    local function walk(value, depth, hemisphere)
        if type(value) ~= 'table' or depth > 6 then return end
        for key, item in pairs(value) do
            local side = (key == 'upperHemisphere' and 'upper') or (key == 'lowerHemisphere' and 'lower') or hemisphere
            if type(item) == 'number' and (key == 'headOn' or key == 'tailOn') then
                local into = data[side or 'upper']
                into[key] = math.max(into[key], item)
                if not side then data.lower[key] = math.max(data.lower[key], item) end
                data.range = math.max(data.range, item)
            else
                walk(item, depth + 1, side)
            end
        end
    end
    if ok and type(sensors) == 'table' then walk(sensors[Unit.SensorType.RADAR], 0, nil) end
    if data.range <= 0 then
        data.range = DEFAULT_RANGE_M
        data.upper = {headOn = DEFAULT_RANGE_M, tailOn = DEFAULT_RANGE_M}
        data.lower = {headOn = DEFAULT_RANGE_M, tailOn = DEFAULT_RANGE_M}
    end
    return data
end

-- The target's radar cross section (m2) and where it came from.
local function target_rcs(unit)
    local ok, desc = pcall(unit.getDesc, unit)
    if ok and type(desc) == 'table' and type(desc.RCS) == 'number' and desc.RCS > 0 then return desc.RCS, 'dcs' end
    local table_rcs = DCSInstructorRadar_RCS and DCSInstructorRadar_RCS[unit:getTypeName()]
    if type(table_rcs) == 'number' then return table_rcs, 'table' end
    return DEFAULT_RCS_M2, 'default'
end

-- How far this radar detects this target now: hemisphere and aspect from DCS's data, scaled by the target's RCS.
local function detection_distance(info, radar_point, target_point, velocity, rcs)
    local hemisphere = target_point.y > radar_point.y + ANTENNA_HEIGHT_M and info.upper or info.lower
    -- Towards the radar (head-on) when its velocity points at the radar; standing still: the larger distance.
    local tx, ty, tz = radar_point.x - target_point.x, radar_point.y - target_point.y, radar_point.z - target_point.z
    local closing = velocity.x * tx + velocity.y * ty + velocity.z * tz
    local speed = math.sqrt(velocity.x ^ 2 + velocity.y ^ 2 + velocity.z ^ 2)
    local base
    if speed < 1 then base = math.max(hemisphere.headOn, hemisphere.tailOn)
    elseif closing > 0 then base = hemisphere.headOn
    else base = hemisphere.tailOn end
    if base <= 0 then base = math.max(hemisphere.headOn, hemisphere.tailOn, info.range) end
    return base * (rcs / REFERENCE_RCS_M2) ^ 0.25
end

local function find_radars()
    local bases = {}
    for _, airbase in ipairs(world.getAirbases() or {}) do
        local ok, name = pcall(airbase.getName, airbase)
        if ok and name then bases[#bases + 1] = {name = name, key = norm(name)} end
    end
    local found = {}
    for _, side in pairs(coalition.side) do
        for _, category in ipairs({Group.Category.GROUND, Group.Category.SHIP}) do
            for _, group in ipairs(coalition.getGroups(side, category) or {}) do
                local group_name = norm(group:getName())
                for _, unit in ipairs(group:getUnits() or {}) do
                    local ok, radar = pcall(unit.hasSensors, unit, Unit.SensorType.RADAR)
                    if ok and radar then
                        local base = owner(norm(unit:getName()), bases) or owner(group_name, bases)
                        if base then
                            found[base.name] = found[base.name] or {}
                            local info = radar_data(unit)
                            info.name = unit:getName()
                            table.insert(found[base.name], info)
                        end
                    end
                end
            end
        end
    end
    return found
end

-- Every airborne aircraft in the mission, any coalition.
local function airborne()
    local list = {}
    for _, side in pairs(coalition.side) do
        for _, category in ipairs({Group.Category.AIRPLANE, Group.Category.HELICOPTER}) do
            for _, group in ipairs(coalition.getGroups(side, category) or {}) do
                for _, unit in ipairs(group:getUnits() or {}) do
                    local ok, flying = pcall(function() return unit:isExist() and unit:inAir() end)
                    if ok and flying then list[#list + 1] = unit end
                end
            end
        end
    end
    return list
end

local function contact(unit, player_name, rcs, rcs_source, range_m, dist_m)
    local point, velocity = unit:getPoint(), unit:getVelocity()
    local lat, lon = coord.LOtoLL(point)
    local name = unit:getName()
    local callsign_ok, callsign = pcall(unit.getCallsign, unit)
    return {unit = name, type = unit:getTypeName(), callsign = callsign_ok and callsign or nil,
            player = (name == player_name) or nil, lat = lat, lon = lon, alt_m = point.y,
            vx = velocity.x, vy = velocity.y, vz = velocity.z,
            rcs_m2 = rcs, rcs_source = rcs_source, range_km = math.floor(range_m / 100 + 0.5) / 10,
            dist_km = math.floor(dist_m / 100 + 0.5) / 10}
end

local function distance(a, b)
    return math.sqrt((a.x - b.x) ^ 2 + (a.y - b.y) ^ 2 + (a.z - b.z) ^ 2)
end

local function in_sight(radar_point, target_point)
    local antenna = {x = radar_point.x, y = radar_point.y + ANTENNA_HEIGHT_M, z = radar_point.z}
    return land.isVisible(antenna, target_point)
end

-- Names of the aircraft a radar's own AI detects by radar (enemies only).
local function ai_detected(radar)
    local names = {}
    local ok, targets = pcall(function()
        return radar:getController():getDetectedTargets(Controller.Detection.RADAR)
    end)
    if ok and type(targets) == 'table' then
        for _, target in pairs(targets) do
            local got, name = pcall(function() return target.object:getName() end)
            if got and name then names[name] = true end
        end
    end
    return names
end

local function enemies(radar, unit)
    local ok, result = pcall(function()
        local a, b = radar:getCoalition(), unit:getCoalition()
        return a ~= b and a ~= 0 and b ~= 0
    end)
    return ok and result
end

-- A unit's own radar picture (user, 2026-11-07: a ship's agent hears of every contact its radar has, friend or foe):
-- the same model as the airfield radars (DCS's sensor distances, the target's RCS, terrain), from SHIP_RADAR_M up,
-- for the aircraft; ships within the radar horizon between masts (SHIP_MAST_M each) and the radar's range. Any
-- coalition, each contact with its side as IFF would tell it. Added beside scan(), which it does not change.
local SHIP_RADAR_M = 30
local SHIP_MAST_M = 20
local OWN_CONTACTS_SHOWN = 20
-- Real search radar ranges for ship types whose DCS data is far below the real ship (user, 2026-11-08). Why: flown
-- 2026-11-08, DCS's getSensors gives the Nimitz-class carriers ("carrier search radar") only 20 km of air detection
-- (their unit file says 50 km), so Mother did not see a Hornet at 65 km, while the real carrier's AN/SPS-48E (3D air
-- search) reaches about 220 nm (~400 km) and its AN/SPS-49 about 250 nm. The Ticonderoga's DCS data (174 km) is kept.
-- Used as the radar's air detection distance for a REFERENCE_RCS_M2 target, then scaled by RCS like DCS's data; the
-- radar horizon (land.isVisible from SHIP_RADAR_M) still hides low aircraft. Only own_contacts uses this: the airfield
-- radars (scan) are untouched. A type not listed keeps DCS's data.
local REAL_SHIP_RADAR_M = {
    CVN_71 = 400000, CVN_72 = 400000, CVN_73 = 400000, Stennis = 400000, CVN_75 = 400000, -- AN/SPS-48E
}
R.own_info = R.own_info or {}
local function own_radar_data(unit)
    local info = radar_data(unit)
    local real = REAL_SHIP_RADAR_M[unit:getTypeName()]
    if real and real > info.range then
        info.range = real
        info.upper = {headOn = real, tailOn = real}
        info.lower = {headOn = real, tailOn = real}
    end
    return info
end
function R.own_contacts(unit, player_name, limit)
    local name = unit:getName()
    local okr, has = pcall(unit.hasSensors, unit, Unit.SensorType.RADAR)
    if not okr or not has then return nil end
    R.own_info[name] = R.own_info[name] or own_radar_data(unit)
    local info, here, own_side = R.own_info[name], unit:getPoint(), unit:getCoalition()
    local antenna = {x = here.x, y = here.y + SHIP_RADAR_M, z = here.z}
    local function side_of(other)
        local ok, s = pcall(other.getCoalition, other)
        if not ok then return 'unknown' end
        return s == own_side and 'friend' or (s == 0 and 'neutral' or 'enemy')
    end
    local list = {}
    for _, target in ipairs(airborne()) do
        pcall(function()
            if target:getName() == name then return end
            local point, velocity = target:getPoint(), target:getVelocity()
            local rcs = target_rcs(target)
            local reach = detection_distance(info, here, point, velocity, rcs)
            local dist = distance(here, point)
            if dist <= reach and land.isVisible(antenna, point) then
                local entry = contact(target, player_name, rcs, nil, reach, dist)
                entry.side, entry.kind, entry.rcs_m2, entry.rcs_source = side_of(target), 'air', nil, nil
                -- Its group, so a controller (controller/desk.py) shows a formation as one track; never shown.
                entry.track_group = target:getGroup():getName()
                list[#list + 1] = entry
            end
        end)
    end
    -- The antenna's height above the ground (an AWACS flies kilometres up; a ship's is SHIP_RADAR_M).
    local ground_ok, ground = pcall(land.getHeight, {x = here.x, y = here.z})
    local antenna_agl = math.max(1, antenna.y - (ground_ok and ground or 0))
    local horizon = 4120 * (math.sqrt(antenna_agl) + math.sqrt(SHIP_MAST_M))
    local own_group = unit:getGroup():getName()
    for _, s in pairs(coalition.side) do
        for _, group in ipairs(coalition.getGroups(s, Group.Category.SHIP) or {}) do
            if group:getName() ~= own_group then
                for _, target in ipairs(group:getUnits() or {}) do
                    pcall(function()
                        if not target:isExist() then return end
                        local point = target:getPoint()
                        local dist = distance(here, point)
                        local mast = {x = point.x, y = point.y + SHIP_MAST_M, z = point.z}
                        if dist <= math.min(horizon, info.range) and land.isVisible(antenna, mast) then
                            local entry = contact(target, player_name, nil, nil, math.min(horizon, info.range), dist)
                            entry.side, entry.kind, entry.group = side_of(target), 'surface', group:getName()
                            list[#list + 1] = entry
                        end
                    end)
                end
            end
        end
    end
    -- The pilot first (never cut off the list), then the nearest.
    table.sort(list, function(a, b)
        if (a.player or false) ~= (b.player or false) then return a.player == true end
        return a.dist_km < b.dist_km
    end)
    while #list > (limit or OWN_CONTACTS_SHOWN) do table.remove(list) end
    return {range_km = math.floor(info.range / 1000 + 0.5), contacts = list}
end

-- Passive detection (ESM) for a controller's sensors (controller/desk.py, user 2026-11-08: "does Magic learn of the
-- SAM"): an AWACS's radar does not see ground units, but its receivers hear an enemy radar that is switched on. Every
-- enemy ground unit or ship with a radar that DCS says is on (Unit:getRadar()), within ESM_RANGE_M and in line of sight
-- from the sensor. A silent radar is heard by nobody. The radar units are looked up again every REFIND_S.
local ESM_RANGE_M = 450000
local ESM_SHOWN = 20
R.emitter_units, R.emitters_found_at = nil, nil
function R.emitters(unit)
    local now = timer.getTime()
    if not R.emitter_units or now - (R.emitters_found_at or -REFIND_S) >= REFIND_S then
        local found = {}
        for _, side in pairs(coalition.side) do
            for _, category in ipairs({Group.Category.GROUND, Group.Category.SHIP}) do
                for _, group in ipairs(coalition.getGroups(side, category) or {}) do
                    for _, member in ipairs(group:getUnits() or {}) do
                        local ok, has = pcall(member.hasSensors, member, Unit.SensorType.RADAR)
                        if ok and has then found[#found + 1] = member end
                    end
                end
            end
        end
        R.emitter_units, R.emitters_found_at = found, now
    end
    local here, own_side = unit:getPoint(), unit:getCoalition()
    local antenna = {x = here.x, y = here.y + SHIP_RADAR_M, z = here.z}
    local list = {}
    for _, emitter in ipairs(R.emitter_units) do
        pcall(function()
            if not emitter:isExist() or emitter:getLife() <= 0 then return end
            local side = emitter:getCoalition()
            if side == own_side or side == 0 then return end
            local on, tracking = emitter:getRadar()
            if not on then return end
            local point = emitter:getPoint()
            local dist = distance(here, point)
            if dist > ESM_RANGE_M or not land.isVisible(antenna, {x = point.x, y = point.y + 5, z = point.z}) then
                return
            end
            local lat, lon = coord.LOtoLL(point)
            list[#list + 1] = {type = emitter:getTypeName(), group = emitter:getGroup():getName(), lat = lat,
                               lon = lon, tracking = tracking ~= nil, dist_km = math.floor(dist / 100 + 0.5) / 10}
        end)
    end
    table.sort(list, function(a, b) return a.dist_km < b.dist_km end)
    while #list > ESM_SHOWN do table.remove(list) end
    return list
end

function R.scan(player_name)
    local now = timer.getTime()
    if not R.radars or now - (R.found_at or -REFIND_S) >= REFIND_S then
        R.radars, R.found_at = find_radars(), now
    end
    local aircraft = airborne()
    local result = {}
    for base, radars in pairs(R.radars) do
        local entry, seen = {radars = {}, range_km = {}, contacts = {}, calibration = {}}, {}
        for _, info in ipairs(radars) do
            local radar = Unit.getByName(info.name)
            if radar and radar:isExist() and radar:isActive() then
                entry.radars[#entry.radars + 1] = info.name
                entry.range_km[info.name] = math.floor(info.range / 1000 + 0.5)
                local radar_point = radar:getPoint()
                local ai = nil   -- asked only when an enemy is near enough to matter
                for _, unit in ipairs(aircraft) do
                    local ok, err = pcall(function()
                        local point, velocity = unit:getPoint(), unit:getVelocity()
                        local rcs, source = target_rcs(unit)
                        local reach = detection_distance(info, radar_point, point, velocity, rcs)
                        local dist = distance(radar_point, point)
                        local detected = dist <= reach and in_sight(radar_point, point)
                        if detected and not seen[unit:getName()] then
                            seen[unit:getName()] = true
                            entry.contacts[#entry.contacts + 1] = contact(unit, player_name, rcs, source, reach, dist)
                        end
                        if dist <= info.range * CALIBRATION_FACTOR and enemies(radar, unit) then
                            ai = ai or ai_detected(radar)
                            entry.calibration[#entry.calibration + 1] = {
                                radar = info.name, unit = unit:getName(), type = unit:getTypeName(), rcs_m2 = rcs,
                                dist_km = math.floor(dist / 100 + 0.5) / 10,
                                model_km = math.floor(reach / 100 + 0.5) / 10,
                                model = detected, ai = ai[unit:getName()] or false}
                        end
                    end)
                    if not ok then env.info('DCS-Instructor radar: ' .. tostring(err)) end
                end
            end
        end
        result[base] = entry
    end
    return DCSInstructorRadar_encode(result)
end
