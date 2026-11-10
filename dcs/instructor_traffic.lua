-- DCS-Instructor mission traffic, for the ATC agent (atc/). Runs inside DCS's mission scripting environment, sent there
-- by dcs/instructor_hook.lua together with dcs/instructor_radar.lua (which provides DCSInstructorRadar_encode).
--
-- The ATC sees all mission traffic (user's choice, 2026-11-02): every aircraft and helicopter of any coalition, on the
-- ground or airborne, including the player (flagged, so the ATC knows the player's coalition).
-- DCSInstructorTraffic.read(player_unit_name) returns JSON: {aircraft: [{unit, type, callsign, side, helicopter,
-- player, lat, lon, alt_m, vx, vy, vz (m/s, DCS axes: x north, z east, y up), in_air}]}.
DCSInstructorTraffic = {}

local function entry(unit, side, helicopter, player_name)
    local point, velocity = unit:getPoint(), unit:getVelocity()
    local lat, lon = coord.LOtoLL(point)
    local name = unit:getName()
    local callsign_ok, callsign = pcall(unit.getCallsign, unit)
    local air_ok, in_air = pcall(unit.inAir, unit)
    local side_ok, own_side = pcall(unit.getCoalition, unit)
    return {unit = name, type = unit:getTypeName(), callsign = callsign_ok and callsign or nil,
            side = side_ok and own_side or side,
            helicopter = helicopter or nil, player = (name == player_name) or nil, lat = lat, lon = lon,
            alt_m = point.y, vx = velocity.x, vy = velocity.y, vz = velocity.z, in_air = air_ok and in_air or false}
end

function DCSInstructorTraffic.read(player_name)
    local list = {}
    for _, side in pairs(coalition.side) do
        for _, category in ipairs({Group.Category.AIRPLANE, Group.Category.HELICOPTER}) do
            for _, group in ipairs(coalition.getGroups(side, category) or {}) do
                for _, unit in ipairs(group:getUnits() or {}) do
                    local ok, item = pcall(function()
                        if unit:isExist() then
                            return entry(unit, side, category == Group.Category.HELICOPTER, player_name)
                        end
                    end)
                    if ok and item then list[#list + 1] = item end
                end
            end
        end
    end
    return DCSInstructorRadar_encode({aircraft = list})
end
