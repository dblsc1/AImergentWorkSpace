// 从 ai-pet-7in1 固定提交下载素材（desktop.pet.v1「三·2」）：断点续传，按 manifest.json 校验大小与 SHA-256。
// 上游 webm 走 Git LFS，所以从 media.githubusercontent.com 取；清单里的大小与 sha 是 LFS 指针里的真值。
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');

const DEFAULT_BASE = 'https://media.githubusercontent.com/media/Houraiji9/ai-pet-7in1';

// 每个 mood 至少一段才成包；只收清单里有的文件。
function buildPack(kind, role, moodMap) {
  const have = new Set(role.files.map(f => f.name));
  const moods = {};
  for (const m of Object.keys(moodMap)) {
    moods[m] = moodMap[m].filter(n => have.has(n));
    if (!moods[m].length) return null;
  }
  return { kind, name: role.name, moods, license: role.license, credits: role.credits, source: role.source };
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
  const res = await fetchFn(url, resume ? { headers: { Range: `bytes=${have}-` } } : {});
  if (res.status !== 200 && res.status !== 206) throw new Error(`HTTP ${res.status}`);
  const append = resume && res.status === 206;
  await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(part, { flags: append ? 'a' : 'w' }));
}

async function fetchOne(role, spec, dir, url, fetchFn) {
  const file = path.join(dir, spec.name), part = file + '.part';
  if (fs.existsSync(file) && fs.statSync(file).size === spec.size) return;
  for (let attempt = 0; attempt < 2; attempt++) {
    await fetchTo(url, part, spec, fetchFn);
    if (await verify(part, spec)) return fs.renameSync(part, file);
    fs.rmSync(part, { force: true });
  }
  throw new Error('校验失败');
}

// 返回 {failed: ['<kind>/<name>', ...]}；某个文件失败不影响其他文件。
async function downloadAll({ manifest, moodMap, destDir, baseUrl = DEFAULT_BASE, fetch: fetchFn = fetch, onProgress = () => {}, onRoleDone = () => {} }) {
  const failed = [];
  for (const [kind, role] of Object.entries(manifest.roles)) {
    const dir = path.join(destDir, kind);
    fs.mkdirSync(dir, { recursive: true });
    const okNames = new Set();
    for (const spec of role.files) {
      const id = `${kind}/${spec.name}`;
      if (/[\\/]|^\.+$/.test(spec.name)) { failed.push(id); continue; }
      const url = `${baseUrl}/${manifest.commit}/${role.dir}/${encodeURIComponent(spec.name)}`;
      try { await fetchOne(role, spec, dir, url, fetchFn); okNames.add(spec.name); onProgress(id); }
      catch (e) { failed.push(id); onProgress(id, e); }
    }
    const pack = buildPack(kind, { ...role, files: role.files.filter(f => okNames.has(f.name)) }, moodMap);
    if (pack && !role.files.some(f => !okNames.has(f.name))) {
      fs.writeFileSync(path.join(dir, 'pack.json'), JSON.stringify(pack, null, 1) + '\n');
      onRoleDone(kind);
    }
  }
  return { failed };
}

module.exports = { downloadAll, buildPack, DEFAULT_BASE };
