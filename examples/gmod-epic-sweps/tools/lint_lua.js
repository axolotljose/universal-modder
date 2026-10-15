// lint_lua.js — parse-check every .lua file in a folder tree (Lua 5.1, GMod's dialect).
//
//   node tools/lint_lua.js epic_sweps/lua
//
// Cheap and worth running before anything else: GMod reports Lua syntax errors in a console nobody
// reads while they are still easy to fix, and a single unbalanced `end` takes down every realm at once.
// Needs `luaparse` (dev only): npm i luaparse
'use strict';
const luaparse = require('luaparse');
const fs = require('fs');
const path = require('path');

const root = process.argv[2] || 'lua';
const files = [];
(function walk(d) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    const s = fs.statSync(p);
    if (s.isDirectory()) walk(p);
    else if (p.endsWith('.lua')) files.push(p);
  }
})(root);

let bad = 0;
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  try {
    luaparse.parse(src, { luaVersion: '5.1', comments: false, scope: false });
    console.log('ok    ' + path.relative(root, f) + ' (' + src.split('\n').length + ' lines)');
  } catch (e) {
    bad++;
    const line = e.line || 0;
    const lines = src.split('\n');
    console.log('FAIL  ' + path.relative(root, f) + ' line ' + line + ' - ' + e.message);
    for (let i = Math.max(0, line - 2); i < Math.min(lines.length, line + 1); i++) {
      console.log('      ' + (i + 1) + ': ' + lines[i]);
    }
  }
}
console.log(bad === 0 ? '\nALL ' + files.length + ' LUA FILES PARSE' : '\n' + bad + ' FILE(S) BROKEN');
process.exit(bad ? 1 : 0);
