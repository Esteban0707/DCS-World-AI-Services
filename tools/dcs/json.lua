-- Minimal JSON encoder for the offline DCS tools (luae has none). Tables with a metatable are written as their
-- tostring(); arrays are tables with keys 1..n only.
local function encode(value)
    local kind = type(value)
    if kind == 'nil' then return 'null' end
    if kind == 'boolean' then return tostring(value) end
    if kind == 'number' then
        if value ~= value or value == math.huge or value == -math.huge then return 'null' end
        return string.format('%.10g', value)
    end
    if kind == 'string' then
        return '"' .. value:gsub('[%c"\\]', function(c)
            local map = {['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t'}
            return map[c] or string.format('\\u%04x', c:byte())
        end) .. '"'
    end
    if kind == 'table' then
        if getmetatable(value) then return encode(tostring(value)) end
        local n = #value
        local is_array = true
        for k in pairs(value) do
            if type(k) ~= 'number' or k < 1 or k > n or k % 1 ~= 0 then is_array = false break end
        end
        local parts = {}
        if is_array then
            for i = 1, n do parts[i] = encode(value[i]) end
            return '[' .. table.concat(parts, ',') .. ']'
        end
        local keys = {}
        for k in pairs(value) do keys[#keys + 1] = k end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        for _, k in ipairs(keys) do parts[#parts + 1] = encode(tostring(k)) .. ':' .. encode(value[k]) end
        return '{' .. table.concat(parts, ',') .. '}'
    end
    return encode(tostring(value))
end

return encode
