-- Dump a DCS terrain's beacons and airfield radios as JSON on stdout (read-only).
-- Usage (working directory = the DCS install, so the terrain files' dofile('Scripts/...') resolve):
--   luae.exe dump_terrain.lua <terrain folder>
local folder = assert(arg[1], 'usage: dump_terrain.lua <terrain folder>')
local encode = dofile((arg[0]:match('^(.*[/\\])') or '') .. 'json.lua')

local real_require = require
require = function(name)
    if name == 'i_18n' then return {translate = function(text) return text end} end
    return real_require(name)
end

local function load(file, global)
    local chunk = loadfile(folder .. '/' .. file)
    if not chunk then return nil end
    local env = setmetatable({}, {__index = _G})
    setfenv(chunk, env)
    local ok, err = pcall(chunk)
    if not ok then io.stderr:write(file .. ': ' .. tostring(err) .. '\n') return nil end
    return env[global]
end

local beacons = load('Beacons.lua', 'beacons') or {}
local radio = load('Radio.lua', 'radio') or {}

-- Constant names (BEACON_TYPE_*, MODULATIONTYPE_*, band keys) defined by DCS's own Scripts/World/Radio files.
local names = {beacon_types = {}, modulations = {}, bands = {}}
for key, value in pairs(_G) do
    if type(key) == 'string' and type(value) == 'number' then
        if key:match('^BEACON_TYPE_') then names.beacon_types[tostring(value)] = key:sub(13) end
        if key:match('^MODULATIONTYPE_') then names.modulations[tostring(value)] = key:sub(16) end
        if key == 'HF' or key == 'VHF_LOW' or key == 'VHF_HI' or key == 'UHF' then names.bands[tostring(value)] = key end
    end
end

-- Band keys of the radio frequency tables are numbers: write them as their band names.
for _, entry in ipairs(radio) do
    if type(entry.frequency) == 'table' then
        local named = {}
        for band, pair in pairs(entry.frequency) do
            named[names.bands[tostring(band)] or tostring(band)] = pair
        end
        entry.frequency = named
    end
end

io.write(encode({beacons = beacons, radio = radio, names = names}))
