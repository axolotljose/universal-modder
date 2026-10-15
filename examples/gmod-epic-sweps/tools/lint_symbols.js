// lint_symbols.js — check that every `<Namespace>.<module>.<field>` the addon *reads* is also
// *written* somewhere in the addon, following the `local U = EpicSWEPs.Util` aliases the files use.
//
//   node tools/lint_symbols.js epic_sweps
//
// GMod resolves these at call time, so a typo or a renamed helper is a runtime error for whoever pulls
// that trigger — usually in the middle of the show. This finds it statically, in a second, without the
// game. Pure data (`Config`, `CVars`) is exempt; see DATA_PREFIXES.
'use strict';
const fs = require('fs');
const path = require('path');

const root = process.argv[2] || 'epic_sweps';
const NS = process.env.UM_NAMESPACE || 'EpicSWEPs';
const DATA_PREFIXES = new Set(['Config', 'CVars']);   // tables read/written by eye, not worth checking

const files = [];
(function walk(d) {
  if (!fs.existsSync(d)) { console.error('no such folder: ' + d); process.exit(2); }
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    const s = fs.statSync(p);
    if (s.isDirectory()) walk(p);
    else if (p.endsWith('.lua')) files.push(p);
  }
})(path.join(root, 'lua'));

const provided = new Map();   // "Mod.field" -> where it is written
const wanted = new Map();     // "Mod.field" -> where it is read
const tables = new Set();     // module tables that exist at all
const where = (map, k, at) => { if (!map.has(k)) map.set(k, []); if (map.get(k).length < 6) map.get(k).push(at); };

const RE_NS_WRITE = new RegExp('(?:^|[^\\w.])' + NS + '\\.([A-Za-z_0-9]+)[.:]([A-Za-z_0-9]+)\\s*(?:\\([^)]*\\))?\\s*=', 'g');
const RE_NS_FN = new RegExp('function\\s+' + NS + '\\.([A-Za-z_0-9]+)[.:]([A-Za-z_0-9]+)', 'g');
const RE_NS_READ = new RegExp('(?:^|[^\\w.])' + NS + '\\.([A-Za-z_0-9]+)\\.([A-Za-z_0-9]+)(?![\\w.(]*\\s*=)', 'g');
const RE_TABLE = new RegExp(NS + '\\.([A-Za-z_0-9]+)\\s*=', 'g');

for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  const rel = path.relative(root, f);

  // per-file aliases: `local U = EpicSWEPs.Util`, `EpicSWEPs.Util = M`, and the module names themselves
  const alias = new Map([[NS, '']]);
  let m;
  // only a *complete* assignment counts, or `local pos = EpicSWEPs.Zombies.GroundPos(x)` invents an alias
  const aRe = new RegExp('^\\s*local\\s+([A-Za-z_0-9]+)\\s*=\\s*' + NS + '\\.([A-Za-z_0-9]+)\\s*$', 'gm');
  while ((m = aRe.exec(src))) alias.set(m[1], m[2]);
  const sRe = new RegExp('^\\s*' + NS + '\\.([A-Za-z_0-9]+)\\s*=\\s*([A-Za-z_0-9]+)\\s*$', 'gm');
  while ((m = sRe.exec(src))) alias.set(m[2], m[1]);
  for (const name of alias.values()) if (name) alias.set(name, name);

  src.split('\n').forEach((ln, i) => {
    const at = rel + ':' + (i + 1);
    while ((m = RE_TABLE.exec(ln))) tables.add(m[1]);
    RE_NS_WRITE.lastIndex = 0;
    while ((m = RE_NS_WRITE.exec(ln))) provided.set(m[1] + '.' + m[2], at);
    RE_NS_FN.lastIndex = 0;
    while ((m = RE_NS_FN.exec(ln))) provided.set(m[1] + '.' + m[2], at);
    RE_NS_READ.lastIndex = 0;
    while ((m = RE_NS_READ.exec(ln))) where(wanted, m[1] + '.' + m[2], at);
    // module-local form: `function M.Name(` / `M.Name = function` / `M.Name = {`
    const loc = /(?:function\s+|)([A-Za-z_0-9]+)\.([A-Za-z_0-9]+)\s*(?:\([^)]*\))?\s*=/g;
    while ((m = loc.exec(ln))) {
      const mod = alias.get(m[1]);
      if (!mod) continue;
      provided.set(mod + '.' + m[2], at);
    }
    const locFn = /function\s+([A-Za-z_0-9]+)[.:]([A-Za-z_0-9]+)/g;
    while ((m = locFn.exec(ln))) {
      const mod = alias.get(m[1]);
      if (!mod) continue;
      provided.set(mod + '.' + m[2], at);
    }
    const reads = /([A-Za-z_0-9]+)\.([A-Za-z_0-9]+)/g;
    while ((m = reads.exec(ln))) {
      const mod = alias.get(m[1]);
      if (!mod || mod === m[1] && !tables.has(mod)) continue;
      where(wanted, mod + '.' + m[2], at);
    }
  });
}

const problems = [];
for (const [k, at] of wanted) {
  const mod = k.split('.')[0];
  if (DATA_PREFIXES.has(mod) || !tables.has(mod)) continue;
  if (provided.has(k)) continue;
  problems.push(k);
}

console.log('files: ' + files.length + '   module tables: ' + [...tables].sort().join(', '));
if (problems.length) {
  console.log('\nREAD BUT NEVER WRITTEN (typo, rename, or a file the loader does not include):');
  for (const k of problems.sort()) {
    const from = (wanted.get(k) || []).join(', ');
    console.log('  ' + k + '   <- ' + from);
  }
  process.exit(1);
}
console.log('\nOK: every ' + NS + '.<module>.<field> the addon reads is defined in the addon');
