-- Dump an aircraft's clickable cockpit controls and autostart macro as JSON, using the module's own Lua files.
-- Run with DCS's interpreter: luae.exe dump_cockpit.lua "<module>/Cockpit/Scripts/" controls|macro
-- Read-only: loads the files in a sandboxed global table, writes JSON to stdout.

local script_path, what = arg[1], arg[2]
assert(script_path and (what == 'controls' or what == 'macro' or what == 'commands'),
       'usage: dump_cockpit.lua <scripts dir/> controls|macro|commands')

-- Engine globals the scripts expect but luae does not provide. Unknown names become named placeholders,
-- so e.g. class_type.TUMB serializes as "class_type.TUMB" instead of failing.
local function placeholder(name)
    return setmetatable({}, {
        __index = function(_, key) return placeholder(name .. '.' .. tostring(key)) end,
        __tostring = function() return name end,
        __call = function() return placeholder(name .. '()') end,
    })
end
LockOn_Options = {script_path = script_path}
_ = function(s) return s end
package.preload['i_18n'] = function() return {translate = _} end
setmetatable(_G, {__index = function(_, key) return placeholder(key) end})

-- Reverse lookups: device id -> name, command id -> "table.name".
local function load(file)
    local chunk = assert(loadfile(script_path .. file))
    chunk()
end

local json = dofile((arg[0]:match('^(.*[/\\])') or '') .. 'json.lua')

load('devices.lua')
load('command_defs.lua')
local device_names = {}
for name, id in pairs(devices) do device_names[id] = name end

-- Command ids are per device (3001 exists in every *_commands table), so a bare number is ambiguous.
-- Each command value becomes {id, name}; the cockpit scripts only store these values, they never compute with them.
-- The aircraft scripts dofile command_defs.lua again themselves, so the labelling is re-applied after every dofile.
-- Controls only: the autostart macro does arithmetic on command ids (circuit-breaker ranges), so it keeps raw
-- numbers and tools/build_module.py names its steps by matching (device, id) against the controls.
local Command = {}
Command.__index = Command
local function label_commands()
    for table_name, commands in pairs(_G) do
        if type(commands) == 'table' and not getmetatable(commands) and table_name:match('_commands$') then
            for key, id in pairs(commands) do
                if type(id) == 'number' then commands[key] = setmetatable({id = id, name = table_name .. '.' .. key}, Command) end
            end
        end
    end
end
if what == 'controls' then
    label_commands()
    local raw_dofile = dofile
    dofile = function(path)
        raw_dofile(path)
        label_commands()
    end
end
local function command_fields(c)
    if getmetatable(c) == Command then return c.id, c.name end
    return c, nil
end

-- Autostart condition names (F5_AD_* style globals set by Macro_sequencies.lua), by value.
local function condition_names()
    local by_id = {}
    for name, value in pairs(_G) do
        if type(value) == 'number' and name:match('^%u[%u%d]*_AD_') then by_id[value] = name end
    end
    return by_id
end

if what == 'controls' then
    load('sounds.lua')
    load('clickable_defs.lua')
    load('clickabledata.lua')
    local out = {}
    for id, e in pairs(elements) do
        local actions = {}
        for i, a in ipairs(e.action or {}) do
            local command_id, command_name = command_fields(a)
            actions[i] = {id = command_id, name = command_name}
        end
        out[#out + 1] = {
            id = id, hint = e.hint, device = e.device, device_name = device_names[e.device],
            class = e.class, actions = actions, arg = e.arg, arg_value = e.arg_value, arg_lim = e.arg_lim,
        }
    end
    table.sort(out, function(a, b) return a.id < b.id end)
    io.write(json(out))
elseif what == 'commands' then
    local out = {}
    for table_name, commands in pairs(_G) do
        if type(commands) == 'table' and not getmetatable(commands) and table_name:match('_commands$') then
            out[table_name] = commands
        end
    end
    io.write(json(out))
else
    load('Macro_sequencies.lua')
    local conditions = condition_names()
    local steps = {}
    for i, s in ipairs(start_sequence_full) do
        steps[i] = {
            time = s.time, device = s.device, device_name = device_names[s.device], action = s.action, value = s.value,
            message = s.message, message_timeout = s.message_timeout,
            check_condition = s.check_condition, check_condition_name = conditions[s.check_condition],
        }
    end
    io.write(json(steps))
end
