// 从 ai-pet-7in1 固定提交下载素材（desktop.pet.v1「三·2」）：断点续传，按 manifest.json 校验大小与 SHA-256。
// 上游 webm 走 Git LFS，所以从 media.githubusercontent.com 取；清单里的大小与 sha 是 LFS 指针里的真值。
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');

const STALL_MS = 300000;
const DEFAULT_BASE = 'https://media.githubusercontent.com/media/Houraiji9/ai-pet-7in1';

// 五个 mood 每个至少一段才成包；click / drag / walk 上游缺的段不列，可为空；只收清单里有的文件。
const CORE_MOODS = ['working', 'idle', 'waiting', 'happy', 'worried'];
function buildPack(kind, role, moodMap) {
  const have = new Set(role.files.map(f => f.name));
  const moods = {};
  for (const m of Object.keys(moodMap)) {
    moods[m] = moodMap[m].filter(n => have.has(n));
    if (!moods[m].length && CORE_MOODS.includes(m)) return null;
  }
  return { kind, name: role.name, moods, license: role.license, credits: role.credits, source: role.source };
}

// 静态形象包（desktop.pet.v1「三·5」）：一张图，没有动画；moods 全空，只认 moods 的读方当它没有动作。
function buildStillPack(kind, role) {
  return { kind, name: role.name, image: role.files[0].name, moods: {}, license: role.license, credits: role.credits, source: role.source };
}

// pets/index.json：这台机器上有哪些宠物包（蜂巢页的形象挑选读它）。以后用户上传的形象 = 多一个包目录，这里多一行。
function writeIndex(destDir) {
  const packs = [];
  for (const kind of fs.readdirSync(destDir).sort()) {
    let p;
    try { p = JSON.parse(fs.readFileSync(path.join(destDir, kind, 'pack.json'), 'utf8')); } catch { continue; }
    const idle = (p.moods && p.moods.idle) || [];
    packs.push({ kind, name: p.name || kind, ...(p.image ? { image: p.image } : { cover: idle[0] || null }) });
  }
  fs.writeFileSync(path.join(destDir, 'index.json'), JSON.stringify({ packs }, null, 1) + '\n');
  return packs;
}

const sha256File = f => new Promise((ok, no) => {
  const h = crypto.createHash('sha256');
  fs.createReadStream(f).on('data', d => h.update(d)).on('end', () => ok(h.digest('hex'))).on('error', no);
});

async function verify(file, spec) {
  if (fs.statSync(file).size !== spec.size) return false;
  return !spec.sha256 || (await sha256File(file)) === spec.sha256;
}

async function fetchTo(url, part, spec, fetchFn) {
  const have = fs.existsSync(part) ? fs.statSync(part).size : 0;
  const resume = have > 0 && have < spec.size;
  // 一次请求至多 STALL_MS：连接挂住不动时不会一直等，已下的留在 .part 里下次续
  const res = await fetchFn(url, { ...(resume && { headers: { Range: `bytes=${have}-` } }), signal: AbortSignal.timeout(STALL_MS) });
  if (res.status !== 200 && res.status !== 206) throw new Error(`HTTP ${res.status}`);
  const append = resume && res.status === 206;
  await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(part, { flags: append ? 'a' : 'w' }));
}

// urls：主地址在前，镜像在后；一共试两次，第二次换镜像（没有镜像就还是主地址）。连不上也算一次。
async function fetchOne(role, spec, dir, urls, fetchFn) {
  const file = path.join(dir, spec.name), part = file + '.part';
  if (fs.existsSync(file) && fs.statSync(file).size === spec.size) return;
  let last = new Error('校验失败');
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await fetchTo(urls[attempt % urls.length], part, spec, fetchFn);
      if (await verify(part, spec)) return fs.renameSync(part, file);
      last = new Error('校验失败'); fs.rmSync(part, { force: true });
    } catch (e) { last = e; if (urls.length < 2) throw e; }   // 断了：.part 留着，换镜像接着下
  }
  throw last;
}

// 返回 {failed: ['<kind>/<name>', ...]}；某个文件失败不影响其他文件。
async function downloadAll({ manifest, moodMap, destDir, baseUrl = manifest.base || DEFAULT_BASE, mirrorUrl = baseUrl === manifest.base ? manifest.mirror : null, fetch: fetchFn = fetch, onProgress = () => {}, onRoleDone = () => {} }) {
  const failed = [];
  for (const [kind, role] of Object.entries(manifest.roles)) {
    const dir = path.join(destDir, kind);
    fs.mkdirSync(dir, { recursive: true });
    const okNames = new Set();
    for (const spec of role.files) {
      const id = `${kind}/${spec.name}`;
      if (/[\\/]|^\.+$/.test(spec.name)) { failed.push(id); continue; }
      const tail = `${role.dir}/${encodeURIComponent(spec.name)}`;
      const urls = [`${baseUrl}/${manifest.commit}/${tail}`].concat(mirrorUrl ? [`${mirrorUrl}/${tail}`] : []);
      try { await fetchOne(role, spec, dir, urls, fetchFn); okNames.add(spec.name); onProgress(id); }
      catch (e) { failed.push(id); onProgress(id, e); }
    }
    const pack = moodMap ? buildPack(kind, { ...role, files: role.files.filter(f => okNames.has(f.name)) }, moodMap)
      : okNames.size ? buildStillPack(kind, role) : null;   // 没给 moodMap = 静态形象清单
    if (pack && !role.files.some(f => !okNames.has(f.name))) {
      fs.writeFileSync(path.join(dir, 'pack.json'), JSON.stringify(pack, null, 1) + '\n');
      onRoleDone(kind);
    }
  }
  return { failed };
}

module.exports = { downloadAll, buildPack, buildStillPack, writeIndex, DEFAULT_BASE };
