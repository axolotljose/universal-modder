#!/usr/bin/env node
/* Run the offline smoke test in plain Lua (fengari), no GMod needed.
 *
 *   npm i fengari          (dev only, once)
 *   node tools/run_smoke_test.js [path-to-addon]
 *
 * It concatenates tools/stub_gmod.lua + tools/smoke_test.lua into one chunk so
 * the test can grab the stub API without a module loader, which fengari's
 * sandbox state does not have wired up.
 */
const fs = require('fs');
const path = require('path');
const fengari = require('fengari');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = fengari;

const tools = __dirname;
const root = path.resolve(process.argv[2] || path.join(tools, '..', 'epic_sweps'));

const stub = fs.readFileSync(path.join(tools, 'stub_gmod.lua'), 'utf8');
const test = fs.readFileSync(path.join(tools, 'smoke_test.lua'), 'utf8');

const src = [
  `local ADDON = ${JSON.stringify(root)}`,
  `_G.ADDON = ADDON`,
  `local api = (function()`,
  stub,
  `end)()`,
  `_G.api = api`,
  test,
].join('\n');

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

// GMod's math libs are preloaded globals; make sure `math` exists as a local alias
if (lauxlib.luaL_loadstring(L, to_luastring(src)) !== lua.LUA_OK) {
  console.error('compile error:', to_jsstring(lua.lua_tostring(L, -1)));
  process.exit(1);
}

const status = lua.lua_pcall(L, 0, 0, 0);
if (status !== lua.LUA_OK) {
  console.error('\nRUNTIME FAILURE:', to_jsstring(lua.lua_tostring(L, -1)));
  process.exit(1);
}
process.exit(0);
