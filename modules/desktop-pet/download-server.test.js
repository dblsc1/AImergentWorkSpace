const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { run } = require('./download-server');

const body = Buffer.alloc(200, 7);
const moodMap = { working: ['a.webm'], idle: ['a.webm'], waiting: ['a.webm'], happy: ['a.webm'], worried: ['a.webm'] };
const manifest = {
  commit: 'abc',
  roles: { gpt: { name: 'GPT', dir: 'd', license: 'L', credits: 'C', source: 'S', files: [{ name: 'a.webm', size: body.length, sha256: crypto.createHash('sha256').update(body).digest('hex') }] } },
};

const stills = { commit: 's', roles: { qwen: { name: '千问娘', dir: 'c', license: 'L', credits: 'C', source: 'S', files: [{ name: 'q.png', size: body.length, sha256: manifest.roles.gpt.files[0].sha256 }] } } };

test('run 下到目录、生成 pack.json；再跑不重复请求', async () => {
  let hits = 0;
  const srv = http.createServer((_, res) => { hits++; res.writeHead(200); res.end(body); });
  await new Promise(r => srv.listen(0, '127.0.0.1', r));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ds-'));
  const baseUrl = `http://127.0.0.1:${srv.address().port}`;
  try {
    assert.deepEqual(await run({ destDir: dir, baseUrl, stillsBaseUrl: baseUrl, manifest, stills, moodMap, log: () => {} }), []);
    assert.deepEqual(fs.readFileSync(path.join(dir, 'gpt', 'a.webm')), body);
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'gpt', 'pack.json'), 'utf8')).kind, 'gpt');
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'qwen', 'pack.json'), 'utf8')), { kind: 'qwen', name: '千问娘', image: 'q.png', moods: {}, license: 'L', credits: 'C', source: 'S' });
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dir, 'index.json'), 'utf8')).packs, [{ kind: 'gpt', name: 'GPT', cover: 'a.webm' }, { kind: 'qwen', name: '千问娘', image: 'q.png' }]);
    assert.equal(hits, 2);
    await run({ destDir: dir, baseUrl, stillsBaseUrl: baseUrl, manifest, stills, moodMap, log: () => {} });
    assert.equal(hits, 2);
  } finally { srv.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('下不动时返回失败清单、不抛', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ds-'));
  try {
    const failed = await run({ destDir: dir, baseUrl: 'http://127.0.0.1:1', stillsBaseUrl: 'http://127.0.0.1:1', manifest, stills, moodMap, log: () => {} });
    assert.deepEqual(failed, ['gpt/a.webm', 'qwen/q.png']);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('主地址连不上时换镜像', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ds-'));
  const seen = [];
  const fetchFn = async url => { seen.push(url); if (url.startsWith('http://main/')) throw new Error('fetch failed'); return new Response(body, { status: 200 }); };
  try {
    const failed = await run({ destDir: dir, manifest: { commit: 'abc', roles: {} }, stills: { ...stills, base: 'http://main', mirror: 'http://mirror/s' }, moodMap, fetch: fetchFn, log: () => {} });
    assert.deepEqual(failed, []);
    assert.deepEqual(seen, ['http://main/s/c/q.png', 'http://mirror/s/c/q.png']);
    assert.deepEqual(fs.readFileSync(path.join(dir, 'qwen', 'q.png')), body);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
