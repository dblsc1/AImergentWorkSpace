const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createPetEventServer, loadOrCreateToken } = require('./pet-event-server');

const ev = { v: 1, type: 'agentDone', at: '2026-11-07T10:12:30+08:00', text: 'garden 干完了', mood: 'happy', data: { agent: 'garden' } };

async function boot() {
  const got = [];
  const srv = createPetEventServer({ token: 'tok', onEvent: e => got.push(e) });
  await new Promise(r => srv.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${srv.address().port}/pet-event`;
  return { srv, got, url };
}
const post = (url, body, auth) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(auth ? { Authorization: auth } : {}) }, body: typeof body === 'string' ? body : JSON.stringify(body) });

test('令牌对：204 并交给回调', async () => {
  const { srv, got, url } = await boot();
  try { assert.equal((await post(url, ev, 'Bearer tok')).status, 204); assert.deepEqual(got, [ev]); } finally { srv.close(); }
});

test('没带 / 不对：401，不回调', async () => {
  const { srv, got, url } = await boot();
  try {
    assert.equal((await post(url, ev)).status, 401);
    assert.equal((await post(url, ev, 'Bearer nope')).status, 401);
    assert.equal((await post(url, ev, 'tok')).status, 401);
    assert.equal(got.length, 0);
  } finally { srv.close(); }
});

test('坏请求：404 / 405 / 400 / 413', async () => {
  const { srv, got, url } = await boot();
  try {
    const A = 'Bearer tok';
    assert.equal((await fetch(url.replace('pet-event', 'x'), { method: 'POST', headers: { Authorization: A } })).status, 404);
    assert.equal((await fetch(url, { headers: { Authorization: A } })).status, 405);
    assert.equal((await post(url, '{bad', A)).status, 400);
    assert.equal((await post(url, { ...ev, mood: 'angry' }, A)).status, 400);
    assert.equal((await post(url, { ...ev, type: 'nope' }, A)).status, 400);
    assert.equal((await post(url, { ...ev, v: 2 }, A)).status, 400);
    assert.equal((await post(url, { ...ev, text: 'x'.repeat(61) }, A)).status, 400);
    assert.equal((await post(url, ' '.repeat(70000), A)).status, 413);
    assert.equal(got.length, 0);
  } finally { srv.close(); }
});

test('令牌第一次生成，之后沿用', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pet-'));
  const a = loadOrCreateToken(dir);
  assert.match(a, /^[0-9a-f]{64}$/);
  assert.equal(loadOrCreateToken(dir), a);
  if (process.platform !== 'win32') assert.equal(fs.statSync(path.join(dir, 'pet-token')).mode & 0o077, 0);
});
