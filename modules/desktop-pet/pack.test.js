const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { loadPack, listPacks, pickClip, packForTool, DEFAULT_PACKS_BY_TOOL } = require('./pack');

const pack = { kind: 'x', name: 'X', moods: { working: ['a.webm', 'b.webm', 'c.webm'], idle: ['i.webm'], waiting: [], happy: ['h.webm'], worried: ['w.webm'] }, license: 'L', credits: 'C', source: 'S' };

function petsDir() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'pets-'));
  fs.mkdirSync(path.join(d, 'gpt')); fs.writeFileSync(path.join(d, 'gpt', 'pack.json'), JSON.stringify({ ...pack, kind: 'gpt' }));
  fs.mkdirSync(path.join(d, 'bad')); fs.writeFileSync(path.join(d, 'bad', 'pack.json'), '{oops');
  fs.mkdirSync(path.join(d, 'empty'));
  return d;
}

test('loadPack 读 pack.json 并给出目录；坏的 / 没有的返回 null', () => {
  const d = petsDir();
  const p = loadPack(d, 'gpt');
  assert.equal(p.kind, 'gpt'); assert.equal(p.dir, path.join(d, 'gpt'));
  assert.equal(loadPack(d, 'bad'), null); assert.equal(loadPack(d, 'empty'), null); assert.equal(loadPack(d, '../x'), null);
  assert.deepEqual(listPacks(d).map(p => p.kind), ['gpt']);
});

test('pickClip 按 mood 挑，避开刚播的', () => {
  for (let i = 0; i < 50; i++) assert.notEqual(pickClip(pack, 'working', 'b.webm'), 'b.webm');
  const seen = new Set(); for (let i = 0; i < 100; i++) seen.add(pickClip(pack, 'working', null));
  assert.equal(seen.size, 3);
  assert.equal(pickClip(pack, 'idle', 'i.webm'), 'i.webm');
  assert.equal(pickClip(pack, 'waiting', null), null);
  assert.equal(pickClip(pack, 'nope', null), null);
  assert.equal(pickClip(pack, 'working', 'a.webm', () => 0), 'b.webm');
});

test('packForTool 按 tool 选，查不到用 *', () => {
  assert.equal(packForTool(DEFAULT_PACKS_BY_TOOL, 'codex'), 'gpt');
  assert.equal(packForTool(DEFAULT_PACKS_BY_TOOL, 'claude-code'), 'claude');
  assert.equal(packForTool(DEFAULT_PACKS_BY_TOOL, 'aider'), 'deepseek');
  assert.equal(packForTool(DEFAULT_PACKS_BY_TOOL, null), 'deepseek');
  assert.equal(packForTool({ a: 'x' }, 'b'), null);
});
