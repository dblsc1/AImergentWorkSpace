// 宠物包：pets/<种类>/pack.json（desktop.pet.v1「宠物包与对照」）。
const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_PACKS_BY_TOOL = { 'claude-code': 'claude', codex: 'gpt', 'gemini-cli': 'gemini', '*': 'deepseek' };

function loadPack(petsDir, kind) {
  if (!/^[\w-]+$/.test(kind)) return null;
  const dir = path.join(petsDir, kind);
  try {
    const p = JSON.parse(fs.readFileSync(path.join(dir, 'pack.json'), 'utf8'));
    if (!p || typeof p.moods !== 'object' || !p.moods) return null;
    const safe = n => typeof n === 'string' && n !== '' && !/[\\/]/.test(n) && !/^\.+$/.test(n);
    const moods = {};
    for (const [m, clips] of Object.entries(p.moods)) moods[m] = Array.isArray(clips) ? clips.filter(safe) : [];
    return { ...p, moods, kind, dir };
  } catch { return null; }
}

function listPacks(petsDir) {
  let names = [];
  try { names = fs.readdirSync(petsDir); } catch { /* 还没下 */ }
  return names.sort().map(k => loadPack(petsDir, k)).filter(Boolean);
}

// 按 mood 随机挑一段，避开 last（只有一段时就还是它）。
function pickClip(pack, mood, last, rand = Math.random) {
  const all = (pack.moods && pack.moods[mood]) || [];
  if (!all.length) return null;
  const pool = all.length > 1 ? all.filter(c => c !== last) : all;
  return pool[Math.floor(rand() * pool.length)];
}

function packForTool(packsByTool, tool) {
  return packsByTool[tool] || packsByTool['*'] || null;
}

module.exports = { loadPack, listPacks, pickClip, packForTool, DEFAULT_PACKS_BY_TOOL };
