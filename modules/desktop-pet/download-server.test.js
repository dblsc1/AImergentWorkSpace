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

test('run 下到目录、生成 pack.json；再跑不重复请求', async () => {
  let hits = 0;
  const srv = http.createServer((_, res) => { hits++; res.writeHead(200); res.end(body); });
  await new Promise(r => srv.listen(0, '127.0.0.1', r));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ds-'));
  const baseUrl = `http://127.0.0.1:${srv.address().port}`;
  try {
    assert.deepEqual(await run({ destDir: dir, baseUrl, manifest, moodMap, log: () => {} }), []);
    assert.deepEqual(fs.readFileSync(path.join(dir, 'gpt', 'a.webm')), body);
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, 'gpt', 'pack.json'), 'utf8')).kind, 'gpt');
    assert.equal(hits, 1);
    await run({ destDir: dir, baseUrl, manifest, moodMap, log: () => {} });
    assert.equal(hits, 1);
  } finally { srv.close(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('下不动时返回失败清单、不抛', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ds-'));
  try {
    const failed = await run({ destDir: dir, baseUrl: 'http://127.0.0.1:1', manifest, moodMap, log: () => {} });
    assert.deepEqual(failed, ['gpt/a.webm']);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
