const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { downloadAll, buildPack } = require('./assets-download');

const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const moodMap = { working: ['w.webm'], idle: ['i.webm'], waiting: ['t.webm'], happy: ['h.webm'], worried: ['x.webm'] };
const bodies = { 'w.webm': Buffer.alloc(5000, 1), 'i.webm': Buffer.alloc(300, 2), 't.webm': Buffer.alloc(300, 3), 'h.webm': Buffer.alloc(300, 4), 'x.webm': Buffer.alloc(300, 5) };
const manifest = (over = {}) => ({
  commit: 'abc', repo: 'o/r',
  roles: { gpt: { name: 'GPT', dir: 'assets/gpt/webm', license: 'L', credits: 'C', source: 'S', files: Object.entries(bodies).map(([name, b]) => ({ name, size: b.length, sha256: sha(b), ...(over[name] || {}) })) } },
});

async function server(opts = {}) {
  const hits = [];
  const srv = http.createServer((req, res) => {
    const name = decodeURIComponent(req.url.split('/').pop());
    hits.push({ name, range: req.headers.range });
    let b = bodies[name]; if (!b) { res.writeHead(404); return res.end(); }
    if (opts.corrupt === name) b = Buffer.alloc(b.length, 9);
    const m = /bytes=(\d+)-/.exec(req.headers.range || '');
    if (m && !opts.ignoreRange) { res.writeHead(206); return res.end(b.subarray(+m[1])); }
    res.writeHead(200); res.end(b);
  });
  await new Promise(r => srv.listen(0, '127.0.0.1', r));
  return { srv, hits, base: `http://127.0.0.1:${srv.address().port}/media` };
}
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'dl-'));

test('下载全套、校验、生成 pack.json；再跑一遍不重复下载', async () => {
  const { srv, hits, base } = await server(); const dir = tmp();
  try {
    const r = await downloadAll({ manifest: manifest(), moodMap, destDir: dir, baseUrl: base });
    assert.deepEqual(r.failed, []);
    for (const n in bodies) assert.deepEqual(fs.readFileSync(path.join(dir, 'gpt', n)), bodies[n]);
    const pk = JSON.parse(fs.readFileSync(path.join(dir, 'gpt', 'pack.json'), 'utf8'));
    assert.deepEqual(pk.moods.working, ['w.webm']);
    assert.equal(pk.license, 'L'); assert.equal(pk.source, 'S'); assert.equal(pk.kind, 'gpt');
    assert.ok(hits.every(h => !h.range));
    const n0 = hits.length;
    await downloadAll({ manifest: manifest(), moodMap, destDir: dir, baseUrl: base });
    assert.equal(hits.length, n0);
  } finally { srv.close(); }
});

test('断点续传：.part 有一半，用 Range 接着下', async () => {
  const { srv, hits, base } = await server(); const dir = tmp();
  fs.mkdirSync(path.join(dir, 'gpt'));
  fs.writeFileSync(path.join(dir, 'gpt', 'w.webm.part'), bodies['w.webm'].subarray(0, 2000));
  try {
    const r = await downloadAll({ manifest: manifest(), moodMap, destDir: dir, baseUrl: base });
    assert.deepEqual(r.failed, []);
    assert.equal(hits.find(h => h.name === 'w.webm').range, 'bytes=2000-');
    assert.deepEqual(fs.readFileSync(path.join(dir, 'gpt', 'w.webm')), bodies['w.webm']);
  } finally { srv.close(); }
});

test('服务器不认 Range（回 200 整个）：重新下，结果仍正确', async () => {
  const { srv, base } = await server({ ignoreRange: true }); const dir = tmp();
  fs.mkdirSync(path.join(dir, 'gpt'));
  fs.writeFileSync(path.join(dir, 'gpt', 'w.webm.part'), bodies['w.webm'].subarray(0, 2000));
  try {
    const r = await downloadAll({ manifest: manifest(), moodMap, destDir: dir, baseUrl: base });
    assert.deepEqual(r.failed, []);
    assert.deepEqual(fs.readFileSync(path.join(dir, 'gpt', 'w.webm')), bodies['w.webm']);
  } finally { srv.close(); }
});

test('SHA-256 对不上：丢弃、记失败、不放进成品、不写 pack.json', async () => {
  const { srv, base } = await server({ corrupt: 'w.webm' }); const dir = tmp();
  try {
    const r = await downloadAll({ manifest: manifest(), moodMap, destDir: dir, baseUrl: base });
    assert.deepEqual(r.failed, ['gpt/w.webm']);
    assert.ok(!fs.existsSync(path.join(dir, 'gpt', 'w.webm'))); assert.ok(!fs.existsSync(path.join(dir, 'gpt', 'w.webm.part')));
    assert.ok(!fs.existsSync(path.join(dir, 'gpt', 'pack.json')));
  } finally { srv.close(); }
});

test('sha256 留空只比大小；大小不对失败；404 失败', async () => {
  const { srv, base } = await server({ corrupt: 'i.webm' }); const dir = tmp();
  try {
    const r = await downloadAll({ manifest: manifest({ 'i.webm': { sha256: '' }, 't.webm': { size: 301 } }), moodMap, destDir: dir, baseUrl: base });
    assert.deepEqual(r.failed, ['gpt/t.webm']);
    assert.ok(fs.existsSync(path.join(dir, 'gpt', 'i.webm')));
    const m = manifest({ 'i.webm': { sha256: '' } }); m.roles.gpt.files.push({ name: 'gone.webm', size: 1, sha256: '' });
    const r2 = await downloadAll({ manifest: m, moodMap, destDir: tmp(), baseUrl: base });
    assert.deepEqual(r2.failed, ['gpt/gone.webm']);
  } finally { srv.close(); }
});

test('文件名带路径分隔符直接拒绝', async () => {
  const m = manifest(); m.roles.gpt.files.push({ name: '../evil.webm', size: 1, sha256: '' });
  const dir = tmp();
  const r = await downloadAll({ manifest: m, moodMap, destDir: dir, baseUrl: 'http://127.0.0.1:1/x' });
  assert.ok(r.failed.includes('gpt/../evil.webm'));
  assert.ok(!fs.existsSync(path.join(dir, 'evil.webm')));
});

test('buildPack：只收清单里有的文件，每个 mood 至少一段才算数', () => {
  const role = manifest().roles.gpt;
  const p = buildPack('gpt', role, { ...moodMap, working: ['w.webm', 'nope.webm'] });
  assert.deepEqual(p.moods.working, ['w.webm']);
  assert.equal(buildPack('gpt', role, { ...moodMap, happy: ['nope.webm'] }), null);
  assert.deepEqual(buildPack('gpt', role, { ...moodMap, click: ['nope.webm'] }).moods.click, []);   // 交互三组可为空
});

test('随程序发布的 manifest / mood-map：每个角色每个 mood 都有片，大小与 sha 齐全', () => {
  const mf = require('./manifest.json'); const mm = require('./mood-map.json');
  assert.equal(mf.commit, '08f2c6f3b2771f2c65ff4d6c14c8dfecba9647bc');
  assert.deepEqual(Object.keys(mf.roles).sort(), ['claude', 'deepseek', 'gemini', 'glm', 'gpt', 'grok', 'kimi']);
  for (const [k, r] of Object.entries(mf.roles)) {
    assert.ok(buildPack(k, r, mm), k);
    assert.ok(r.files.every(f => f.size > 0 && /^[0-9a-f]{64}$/.test(f.sha256) && f.name.endsWith('.webm')), k);
    assert.ok(r.license && r.credits && r.source);
  }
});

test('mood-map 的状态动作（hive.stage.v1「三十二」）：chatting / idle-bored / idle-slacking / idle-sleeping 各有片，七个角色的包里都带上', () => {
  const mf = require('./manifest.json'); const mm = require('./mood-map.json');
  for (const k of ['chatting', 'idle-bored', 'idle-slacking', 'idle-sleeping']) assert.ok(mm[k].length >= 1, k);
  for (const [kind, r] of Object.entries(mf.roles)) {
    const p = buildPack(kind, r, mm);
    for (const k of ['chatting', 'idle-bored', 'idle-slacking', 'idle-sleeping']) assert.deepEqual(p.moods[k], mm[k], kind + '/' + k);
  }
});
