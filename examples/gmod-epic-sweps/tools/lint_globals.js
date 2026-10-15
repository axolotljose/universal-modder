// lint_globals.js — every global the addon *reads* must exist in the stub's modelled GMod environment.
//
//   node tools/lint_globals.js epic_sweps
//
// The smoke test only walks the paths it fires; this walks every line, including the cold ones (the
// spawnmenu panel, concommands, TranslateDamage, level shutdown, holster/OnRemove), where a typo'd API
// name otherwise waits for a player to trip it. Scoping comes from luaparse, so locals, params and
// loop variables are correctly ignored and `t.field` is not mistaken for a global read.
//
// The whitelist is produced by running tools/stub_gmod.lua and dumping its environment, so "unknown
// global" also means "an API our fake GMod never modelled" — usually a typo, sometimes a hole in the
// stub. Both are worth a look. Needs `npm i luaparse fengari` (dev only).
'use strict';
const fs = require('fs');
const path = require('path');
const luaparse = require('luaparse');
const fengari = require('fengari');
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = fengari;

const tools = __dirname;
const root = path.resolve(process.argv[2] || path.join(tools, '..', 'epic_sweps'));

// ---- 1. ask the stub what GMod looks like ------------------------------------
const stub = fs.readFileSync(path.join(tools, 'stub_gmod.lua'), 'utf8');
// setup() is what fills the environment, so run it once before reading it back
const src = `local api = (function()\n${stub}\nend)()\napi.stateReset()\nlocal names = {}\n`
  + `for k, v in pairs(api.getG()) do if v ~= nil then names[#names+1] = tostring(k) end end\n`
  + `for k, v in pairs(_G) do if v ~= nil then names[#names+1] = tostring(k) end end\n`
  + `table.sort(names)\nreturn table.concat(names, "\\n")`;
const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);
if (lauxlib.luaL_loadstring(L, to_luastring(src)) !== lua.LUA_OK) {
  console.error('stub compile error:', to_jsstring(lua.lua_tostring(L, -1)));
  process.exit(2);
}
if (lua.lua_pcall(L, 0, 1, 0) !== lua.LUA_OK) {
  console.error('stub run error:', to_jsstring(lua.lua_tostring(L, -1)));
  process.exit(2);
}
const known = new Set(to_jsstring(lua.lua_tostring(L, -1)).split('\n').filter(Boolean));

// Names that exist in real GMod but are not looked up as globals (or are Lua-literal noise).
const NOT_A_GLOBAL = new Set(['self', 'SWEP', 'ENT', 'MODULE', 'KEY', 'key']);

// ---- 2. the true global reads of every file ---------------------------------
const files = [];
(function walk(d) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p);
    else if (p.endsWith('.lua')) files.push(p);
  }
})(path.join(root, 'lua'));

// Globals the addon assigns itself (EpicSWEPs, and any compatibility fallback) are not the game's to provide.
const assigned = new Set();
for (const f of files) {
  let ast;
  try { ast = luaparse.parse(fs.readFileSync(f, 'utf8'), { luaVersion: '5.1', scope: true }); } catch (e) { continue; }
  (function walk(o) {
    if (!o || typeof o !== 'object') return;
    if (o.type === 'AssignmentStatement') {
      for (const v of o.variables || []) if (v.type === 'Identifier' && !v.isLocal) assigned.add(v.name);
    }
    for (const k in o) { const v = o[k]; if (Array.isArray(v)) v.forEach(walk); else if (v && typeof v === 'object') walk(v); }
  })(ast);
}

const report = new Map();
const seen = (name, where) => {
  if (!report.has(name)) report.set(name, []);
  if (report.get(name).length < 5) report.get(name).push(where);
};

for (const f of files) {
  const text = fs.readFileSync(f, 'utf8');
  let ast;
  try {
    ast = luaparse.parse(text, { luaVersion: '5.1', scope: true, comments: false });
  } catch (e) {
    console.log('FAIL  ' + path.relative(root, f) + ' does not parse: ' + e.message);
    process.exit(1);
  }
  for (const id of ast.globals || []) {
    if (id.isLocal || !id.name) continue;
    const name = id.name;
    if (known.has(name) || assigned.has(name) || NOT_A_GLOBAL.has(name)) continue;
    seen(name, path.relative(root, f) + ':' + id.line);
  }
}

console.log('modelled GMod environment: ' + known.size + ' names   files: ' + files.length);
if (report.size === 0) {
  console.log('\nOK: every global the addon reads is modelled by the stub');
  process.exit(0);
}
console.log('\nREAD BUT NOT MODELLED (typo, or a hole in the stub - check both):');
for (const [name, where] of [...report].sort((a, b) => a[0].localeCompare(b[0]))) {
  console.log('  ' + name.padEnd(22) + where.join(', '));
}
process.exit(1);
