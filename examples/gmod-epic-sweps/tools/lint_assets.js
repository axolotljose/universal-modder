// lint_assets.js — check the addon's non-Lua contracts: sound scripts, referenced .wavs, materials, icons.
//
//   node tools/lint_assets.js epic_sweps
//
// Why this exists: every failure mode in these files is *silent*. A malformed soundscript is skipped by
// the engine, a `"level"` key instead of `"soundlevel"` does nothing, repeated `"wave"` lines outside
// `rndwave` collapse to the last one, and a renamed .wav means the weapon fires in total silence. None of
// it shows up in a Lua stack trace, so the Lua linters cannot see it either. This checks the files GMod
// reads without telling you anything.
'use strict';
const fs = require('fs');
const path = require('path');

const root = path.resolve(process.argv[2] || 'epic_sweps');
const errors = [];
const warns = [];
const err = (msg) => errors.push(msg);
const warn = (msg) => warns.push(msg);
const rel = (p) => path.relative(root, p);

// ---------------------------------------------------------------- tiny KeyValues reader
// Returns { name, value } nodes; bare words and "quoted" both allowed, // comments allowed.
function readKV(text, file) {
  let i = 0;
  const n = text.length;
  function ws() {
    for (;;) {
      while (i < n && /\s/.test(text[i])) i++;
      if (text[i] === '/' && text[i + 1] === '/') { while (i < n && text[i] !== '\n') i++; continue; }
      if (text[i] === '/' && text[i + 1] === '/') { while (i < n && text[i] !== '\n') i++; continue; }
      return;
    }
  }
  function str() {
    if (text[i] === '"') {
      i++; const start = i;
      while (i < n && text[i] !== '"') i++;
      if (i >= n) { err(file + ': unterminated string'); return null; }
      const v = text.slice(start, i); i++; return v;
    }
    const start = i;
    while (i < n && /[^\s"{}]/.test(text[i])) i++;
    return text.slice(start, i);
  }
  function block(depth) {
    const out = [];
    for (;;) {
      ws();
      if (i >= n) return out;
      if (text[i] === '}') { i++; return out; }
      const key = str();
      if (key === null || key === '') { err(file + ': parse noise at offset ' + i); return out; }
      ws();
      if (text[i] === '{') {
        i++;
        out.push({ key, kids: block(depth + 1) });
        if (depth > 12) { err(file + ': nested too deep (unbalanced brace?)'); return out; }
      } else {
        const val = str();
        out.push({ key, value: val === null ? '' : val });
      }
    }
  }
  const top = block(0);
  ws();
  if (i < n) err(file + ': trailing garbage at offset ' + i);
  return top;
}

function exists(...p) { return fs.existsSync(path.join(root, ...p)); }
function isValve(p) { return !p.startsWith('epic_sweps/'); }

// ---------------------------------------------------------------- sound scripts
const CHAN = new Set(['CHAN_REPLACE', 'CHAN_AUTO', 'CHAN_WEAPON', 'CHAN_VOICE', 'CHAN_ITEM', 'CHAN_BODY',
  'CHAN_STREAM', 'CHAN_STATIC', 'CHAN_VOICE2', 'CHAN_VOICE_BASE']);
const SNDLVL = new Set(['SNDLVL_NONE', 'SNDLVL_20dB', 'SNDLVL_25dB', 'SNDLVL_30dB', 'SNDLVL_35dB',
  'SNDLVL_40dB', 'SNDLVL_45dB', 'SNDLVL_50dB', 'SNDLVL_55dB', 'SNDLVL_IDLE', 'SNDLVL_TALKING',
  'SNDLVL_60dB', 'SNDLVL_65dB', 'SNDLVL_STATIC', 'SNDLVL_70dB', 'SNDLVL_75dB', 'SNDLVL_NORM',
  'SNDLVL_80dB', 'SNDLVL_85dB', 'SNDLVL_90dB', 'SNDLVL_95dB', 'SNDLVL_100dB', 'SNDLVL_105dB',
  'SNDLVL_110dB', 'SNDLVL_120dB', 'SNDLVL_130dB', 'SNDLVL_GUNFIRE', 'SNDLVL_140dB', 'SNDLVL_150dB',
  'SNDLVL_180dB']);
const KEYS = new Set(['channel', 'volume', 'pitch', 'soundlevel', 'wave', 'rndwave', 'entrymode',
  'duration', 'fade', 'gain', 'dsp', 'dspdist']);
const scripts = new Map();

const scriptsDir = path.join(root, 'scripts');
if (fs.existsSync(scriptsDir)) {
  for (const f of fs.readdirSync(scriptsDir).filter(x => x.endsWith('.txt'))) {
    const file = 'scripts/' + f;
    const text = fs.readFileSync(path.join(scriptsDir, f), 'utf8');
    const top = readKV(text, file);
    let blocks = 0;
    for (const node of top) {
      const groups = /^soundscript$/i.test(node.key) ? (node.kids || []) : [node];
      for (const g of groups) {
        if (!g.kids) continue;
        blocks++;
        const fields = g.kids;
        scripts.set(g.key, fields);
        const where = file + ' "' + g.key + '"';
        let waves = 0;
        for (const fld of fields) {
          if (fld.key === 'level') err(where + ': "level" is the Lua sound.Add key; a .txt soundscript wants "soundlevel"');
          if (!KEYS.has(fld.key)) err(where + ': unknown key "' + fld.key + '"');
          if (fld.key === 'wave') waves++;
          if (fld.key === 'channel' && fld.value) {
            if (!CHAN.has(fld.value) && !(Number(fld.value) >= -1 && Number(fld.value) <= 8)) {
              err(where + ': channel "' + fld.value + '" is neither a CHAN_* name nor -1..8');
            }
            if (fld.value === 'CHAN_STATIC') { /* fine */ }
            if (Number(fld.value) === 1) warn(where + ': channel 1 is CHAN_WEAPON, not CHAN_STATIC (6) - weapon sounds cut each other off');
          }
          if (fld.key === 'soundlevel' && fld.value) {
            if (!SNDLVL.has(fld.value) && !(Number(fld.value) >= 0 && Number(fld.value) <= 511)) {
              err(where + ': soundlevel "' + fld.value + '" is neither an SNDLVL_* name nor 0..511');
            }
          }
          if (fld.key === 'volume' && fld.value) {
            const v = Number(fld.value);
            if (!(v >= 0 && v <= 1)) err(where + ': volume "' + fld.value + '" must be 0..1');
          }
          if (fld.key === 'pitch' && fld.value) {
            const parts = String(fld.value).split(',').map(x => Number(x.trim()));
            for (const v of parts) if (!(v >= 0 && v <= 255)) err(where + ': pitch "' + fld.value + '" parts must be 0..255');
            if (parts.length === 2 && parts[0] > parts[1]) err(where + ': pitch range "' + fld.value + '" is upside down');
            if (parts.length > 2) err(where + ': pitch takes "100" or "min,max"');
          }
          if (fld.key === 'wave' && fld.value && !isValve(fld.value)) {
            if (!exists('sound', fld.value)) err(where + ': wave "' + fld.value + '" is not in this addon (sound/' + fld.value + ' missing)');
          }
        }
        // a randomised group is "rndwave { wave ... }"; sibling wave lines collapse to the last one
        const rnd = fields.filter(f => f.key === 'rndwave');
        if (waves > 1 && !rnd.length) err(where + ': ' + waves + ' sibling "wave" lines with no "rndwave" - KeyValues keeps only the last');
        for (const r of rnd) {
          for (const w of (r.kids || [])) {
            if (w.key !== 'wave') err(where + ': inside rndwave only "wave" is valid, got "' + w.key + '"');
            if (w.value && !isValve(w.value) && !exists('sound', w.value)) err(where + ': wave "' + w.value + '" is not in this addon');
          }
          if (!(r.kids || []).length) err(where + ': empty rndwave');
        }
      }
    }
    console.log('soundscript ' + file + ': ' + blocks + ' entries, parsed clean');
  }
}

// ---------------------------------------------------------------- what the Lua points at
const luaFiles = [];
(function walk(d) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    const st = fs.statSync(p);
    if (st.isDirectory()) walk(p);
    else if (p.endsWith('.lua')) luaFiles.push(p);
  }
})(path.join(root, 'lua'));

const referenced = new Set();
for (const f of luaFiles) {
  const src = fs.readFileSync(f, 'utf8');
  let m;
  for (const s of src.match(/"(epic_sweps\/[A-Za-z0-9_\/.]*)"/g) || []) {
    const p = s.slice(1, -1);
    referenced.add(p);
    if (p.endsWith('.wav')) {
      if (!exists('sound', p)) err(rel(f) + ': plays sound/' + p + ' but that file is not in the addon');
    } else if (p.endsWith('.vmt') || p.endsWith('.png') || p.endsWith('.dxs')) {
      const dir = p.startsWith('materials/') ? '' : 'materials/';
      if (!exists(dir + p)) err(rel(f) + ': references ' + dir + p + ' which does not exist');
    } else if (!p.startsWith('epic_sweps/')) {
      // bare material path, e.g. Material("epic_sweps/flash")
      if (!exists('materials', p + '.vmt')) err(rel(f) + ': Material("' + p + '") has no materials/' + p + '.vmt');
    }
  }
}

// every .wav we ship should be used by something
const snd = path.join(root, 'sound', 'epic_sweps');
if (fs.existsSync(snd)) {
  const scriptWavs = new Set();
  for (const [, fields] of scripts) {
    for (const fld of fields) {
      if (fld.key === 'wave' && fld.value) scriptWavs.add(fld.value);
      if (fld.key === 'rndwave') for (const w of (fld.kids || [])) if (w.value) scriptWavs.add(w.value);
    }
  }
  for (const f of fs.readdirSync(snd)) {
    if (!f.endsWith('.wav')) continue;
    const p = 'epic_sweps/' + f;
    if (!referenced.has(p) && !scriptWavs.has(p)) warn('sound/epic_sweps/' + f + ' is shipped but nothing references it');
  }
}

// ---------------------------------------------------------------- spawnmenu icons
const weaponDir = path.join(root, 'lua', 'weapons');
if (fs.existsSync(weaponDir)) {
  for (const cls of fs.readdirSync(weaponDir)) {
    const has = ['png', 'tga', 'jpg'].some(ext => exists('materials', 'vgui', 'entities', cls + '.' + ext));
    if (!has) err('weapon ' + cls + ': no materials/vgui/entities/' + cls + '.(png|tga|jpg), so the spawnmenu shows a question mark');
  }
}

// ---------------------------------------------------------------- verdict
console.log('\n' + (errors.length ? errors.length + ' error(s), ' : '') + warns.length + ' warning(s)');
for (const e of errors) console.log('  ERR  ' + e);
for (const w of warns) console.log('  warn ' + w);
process.exit(errors.length ? 1 : 0);
