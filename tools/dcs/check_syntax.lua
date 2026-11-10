-- Syntax check of a Lua file with DCS's own interpreter (bin/luae.exe): exit 1 with the error when it does not load.
-- Usage: luae.exe check_syntax.lua <file>
local chunk, err = loadfile(assert(arg[1], 'file to check'))
if not chunk then
    io.stderr:write(tostring(err))
    os.exit(1)
end
