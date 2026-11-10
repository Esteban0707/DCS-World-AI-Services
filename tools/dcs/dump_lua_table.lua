-- Dump one global table from a DCS data file (mission, dictionary...) as JSON on stdout.
-- Usage: luae.exe dump_lua_table.lua <file> <global name>
local path, name = arg[1], arg[2]
assert(path and name, 'usage: dump_lua_table.lua <file> <global name>')
local encode = dofile((arg[0]:match('^(.*[/\\])') or '') .. 'json.lua')
local env = {}
local chunk = assert(loadfile(path))
setfenv(chunk, env)
chunk()
io.write(encode(assert(env[name], 'no global ' .. name .. ' in ' .. path)))
