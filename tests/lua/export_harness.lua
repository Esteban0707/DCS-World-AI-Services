-- Runs dcs/instructor_export.lua outside DCS with fake DCS and SRS globals, and prints every UDP message it sends.
-- Usage: luae.exe export_harness.lua <project dir with trailing slash> [srs|no_srs] [on|off]
--   srs:    SRS's table is reachable (a local upvalue of its global export function, as in SRS 2.4.1.0); the fake
--           SRS exporter mimics DCS-SRS-Modules/F5E.lua and returns the table it was given.
--   no_srs: SRS's table cannot be reached (as seen live in DCS 2026-11-01): radios come from the cockpit devices.
--   on/off: power of the fake AN/ARC-164 (DCS device 23), tuned to 251.0 MHz.
local project = assert(arg[1], 'project dir')
local with_srs = (arg[2] or 'srs') == 'srs'
local radio_on = (arg[3] or 'on') == 'on'
-- arg[4] "mi8": a Mi-8MT read from its cockpit (modules/Mi-8MT/radios.lua): R-863 (device 38) on 255.0 AM,
-- JADRO-1A (37) on 5.0 MHz, R-828 (39) on 40.0 FM; arg[5] = the SPU-9 selector (argument 550): 0.0 R-863, 0.2 R-828,
-- 0.5 none; arg[6] "ics" = the Radio/ICS switch (553) on.
local mi8 = arg[4] == 'mi8'
-- arg[4] "f16": an aircraft with no modules/<aircraft>/radios.lua (F-16C_50, live 2026-11-02): its radios are found
-- by scanning the devices: 36 on 305.0 AM, 38 on 127.0 AM, 40 a navigation receiver on 110.3 MHz (left out).
local f16 = arg[4] == 'f16'
local cockpit_args = {[550] = tonumber(arg[5] or '0') or 0, [553] = arg[6] == 'ics' and 1 or 0, [369] = 0}
local function radio_device(mhz) return {is_on = function() return true end,
                                         get_frequency = function() return mhz * 1e6 end,
                                         get_modulation = function() return 0 end} end
local mi8_devices = {[38] = radio_device(255), [37] = radio_device(5), [39] = radio_device(40)}
local f16_devices = {[36] = radio_device(305), [38] = radio_device(127), [40] = radio_device(110.3)}

log = {INFO = 1, write = function(_, _, text) print('LOG ' .. text) end}

package.preload['socket'] = function()
    return {udp = function()
        return {settimeout = function() end, close = function() end,
                sendto = function(_, data) print('SENT ' .. data) end}
    end}
end

local model_time = 0
function LoGetModelTime() return model_time end
function LoGetSelfData() return {Name = mi8 and 'Mi-8MT' or f16 and 'F-16C_50' or 'F-5E-3', LatLongAlt = {Lat = 42, Long = 41}, Heading = 0, Pitch = 0, Bank = 0} end
function LoGetIndicatedAirSpeed() return 0 end
function LoGetTrueAirSpeed() return 0 end
function LoGetMachNumber() return 0 end
function LoGetAltitudeAboveSeaLevel() return 10 end
function LoGetAltitudeAboveGroundLevel() return 0 end
function LoGetVerticalVelocity() return 0 end
function LoGetAngleOfAttack() return 0 end
function LoGetAccelerationUnits() return {y = 1} end
function LoGetEngineInfo() return {RPM = {left = 0, right = 0}} end
local uhf = {is_on = function() return radio_on end, get_frequency = function() return 251000000.0002 end,
             get_modulation = function() return 0 end}
function GetDevice(id)
    if mi8 and mi8_devices[id] then return mi8_devices[id] end
    if f16 then return f16_devices[id] or {get_argument_value = function(_, arg) return cockpit_args[arg] or 0 end} end
    if id == 23 and not mi8 then return uhf end
    return {get_argument_value = function(_, arg) return cockpit_args[arg] or 0 end}
end

local srs_got = nil
local SR = {exporters = {}, exporter = function() end, readLOSSocket = function() end}
if with_srs then
    LuaExportBeforeNextFrame = function() SR.readLOSSocket() end
end
SR.exporters['F-5E-3'] = function(data, sr)
    data.radios[2].name = 'AN/ARC-164'
    data.radios[2].freq = radio_on and 251000000 or 1
    data.radios[2].modulation = 0
    data.selected = 1
    return data
end
local function srs_frame()   -- what SRS's own SR.exporter() does each frame
    local template = {selected = 0, ptt = false, radios = {
        {name = '', freq = 100, modulation = 2, secFreq = 0},
        {name = '', freq = 0, modulation = 3, secFreq = 0}}}
    srs_got = SR.exporters['F-5E-3'](template, SR)
end

INSTRUCTOR_DIR = project
dofile(project .. 'dcs/instructor_export.lua')
LuaExportStart()
for _ = 1, 3 do
    model_time = model_time + 0.25
    LuaExportAfterNextFrame()
    srs_frame()
end
print('SRS_RESULT ' .. srs_got.radios[2].name .. ' ' .. srs_got.radios[2].freq .. ' selected ' .. srs_got.selected)
