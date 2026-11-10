-- DCS-Instructor airfield weather. Runs inside DCS's mission scripting environment, sent there by
-- dcs/instructor_hook.lua together with dcs/instructor_radar.lua (which provides DCSInstructorRadar_encode).
--
-- DCSInstructorWeather.read() returns JSON: {airfield name: {lat, lon, elevation_m, wind_north, wind_east (m/s, the
-- air's movement 10 m above the field, towards: DCS axes x north, z east), temperature_k, pressure_pa (at the field:
-- QFE), side (coalition), frequencies_hz (the airfield's ATC radios, from its communicator; for the ATC agent),
-- runways: [{name, course (radians, as DCS reports it: minus the grid heading of the "name" end), length_m, width_m,
-- lat, lon (the runway's centre), start_lat/start_lon and end_lat/end_lon (the named end's threshold, where its
-- takeoffs start, and the far end)}]}}. Airdromes only (no FARPs or ships).
-- DCS's vectors (wind, runway course, velocities) are relative to the map's grid north, not true north.
-- DCSInstructorWeather.mission() returns JSON: the mission's visibility, fog, clouds and dust, and the time of day
-- (for the ATC: instrument conditions).
DCSInstructorWeather = {}

local function frequencies(airbase)
    local ok, list = pcall(function()
        local comm = airbase:getCommunicator()
        if not comm or not comm:hasTransiver() then return nil end
        local result = {}
        for i = 0, comm:countTransivers() - 1 do result[#result + 1] = comm:getFrequency(i) end
        return result
    end)
    return ok and list or nil
end

local function airfield(airbase)
    local desc = airbase:getDesc() or {}
    if Airbase and Airbase.Category and desc.category ~= Airbase.Category.AIRDROME then return nil end
    local point = airbase:getPoint()
    local wind = atmosphere.getWind({x = point.x, y = point.y + 10, z = point.z})
    local temperature_k, pressure_pa = atmosphere.getTemperatureAndPressure(point)
    local lat, lon = coord.LOtoLL(point)
    local runways = {}
    for _, runway in pairs(airbase:getRunways() or {}) do
        local entry = {name = tostring(runway.Name or runway.name or ''), course = runway.course,
                       length_m = runway.length, width_m = runway.width}
        if runway.position then
            local p = runway.position
            entry.lat, entry.lon = coord.LOtoLL(p)
            -- Both ends in lat/lon: the course is relative to the map's grid north, which on a transverse Mercator map
            -- differs from true north (about 7 degrees at Senaki, live 2026-11-02); the true heading comes from these.
            if type(runway.course) == 'number' and type(runway.length) == 'number' then
                local h, half = -runway.course, runway.length / 2
                local dx, dz = half * math.cos(h), half * math.sin(h)
                entry.start_lat, entry.start_lon = coord.LOtoLL({x = p.x - dx, y = p.y, z = p.z - dz})
                entry.end_lat, entry.end_lon = coord.LOtoLL({x = p.x + dx, y = p.y, z = p.z + dz})
            end
        end
        runways[#runways + 1] = entry
    end
    local side_ok, side = pcall(airbase.getCoalition, airbase)
    return {lat = lat, lon = lon, elevation_m = point.y, wind_north = wind.x, wind_east = wind.z,
            temperature_k = temperature_k, pressure_pa = pressure_pa, runways = runways,
            side = side_ok and side or nil, frequencies_hz = frequencies(airbase)}
end

function DCSInstructorWeather.read()
    local result = {}
    for _, airbase in ipairs(world.getAirbases() or {}) do
        local ok, info = pcall(airfield, airbase)
        if ok and info then result[airbase:getName()] = info end
    end
    return DCSInstructorRadar_encode(result)
end

function DCSInstructorWeather.mission()
    local ok, w = pcall(function() return env.mission.weather end)
    w = ok and type(w) == 'table' and w or {}
    local fog, clouds, visibility = w.fog or {}, w.clouds or {}, w.visibility or {}
    local time_ok, time_s = pcall(timer.getAbsTime)
    return DCSInstructorRadar_encode({
        visibility_m = visibility.distance, fog = w.enable_fog and true or false, fog_visibility_m = fog.visibility,
        fog_thickness_m = fog.thickness, cloud_preset = clouds.preset, cloud_base_m = clouds.base,
        cloud_density = clouds.density, cloud_thickness_m = clouds.thickness, precipitation = clouds.iprecptns,
        dust = w.enable_dust and true or false, dust_visibility_m = w.dust_density,
        time_of_day_s = time_ok and time_s or nil})
end
