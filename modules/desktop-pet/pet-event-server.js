// 收 PetEvent：只听 127.0.0.1:47340，POST /pet-event，Authorization: Bearer <本机桌宠令牌>。
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const PORT = 47340;
const HOST = '127.0.0.1';
const MAX_BODY = 64 * 1024;
const TYPES = ['focus', 'agentWaiting', 'agentDone', 'alert', 'alertCleared'];
const MOODS = ['working', 'idle', 'waiting', 'happy', 'worried'];

function loadOrCreateToken(dataDir) {
  const f = path.join(dataDir, 'pet-token');
  try { const t = fs.readFileSync(f, 'utf8').trim(); if (t) return t; } catch { /* 第一次 */ }
  fs.mkdirSync(dataDir, { recursive: true });
  const t = crypto.randomBytes(32).toString('hex');
  fs.writeFileSync(f, t + '\n', { mode: 0o600 });
  return t;
}

function tokenOk(header, token) {
  const m = /^Bearer (.+)$/.exec(header || '');
  if (!m) return false;
  const a = crypto.createHash('sha256').update(m[1]).digest();
  const b = crypto.createHash('sha256').update(token).digest();
  return crypto.timingSafeEqual(a, b);
}

function validEvent(e) {
  return e && typeof e === 'object' && e.v === 1 && TYPES.includes(e.type) && MOODS.includes(e.mood)
    && typeof e.text === 'string' && [...e.text].length <= 60;
}

// 返回未监听的 http.Server；调用方 .listen(PORT, HOST)。
function createPetEventServer({ token, onEvent }) {
  return http.createServer((req, res) => {
    const end = (code) => { res.writeHead(code); res.end(); };
    if (req.url !== '/pet-event') return end(404);
    if (req.method !== 'POST') return end(405);
    if (!tokenOk(req.headers.authorization, token)) return end(401);
    if (Number(req.headers['content-length']) > MAX_BODY) { res.writeHead(413, { Connection: 'close' }); res.end(); return req.destroy(); }
    const chunks = []; let n = 0, over = false;
    req.on('data', c => {
      n += c.length;
      if (n > MAX_BODY) { if (!over) { over = true; res.writeHead(413, { Connection: 'close' }); res.end(); req.destroy(); } return; }
      chunks.push(c);
    });
    req.on('end', () => {
      if (over) return;
      let e;
      try { e = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return end(400); }
      if (!validEvent(e)) return end(400);
      try { onEvent(e); } catch (err) { console.error('pet-event handler:', err); }
      end(204);
    });
  });
}

module.exports = { createPetEventServer, loadOrCreateToken, PORT, HOST };
