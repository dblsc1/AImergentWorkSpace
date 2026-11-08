// 打包：node package.js --platform=win32|darwin|linux --arch=x64|arm64 [--tag=v0.6.0]
// 产物 dist/honeycomb-pet-<tag>-<win|mac|linux>-<arch>.zip；不签名、不公证、不带宠物素材。
const { packager } = require('@electron/packager');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const arg = (k, d) => (process.argv.find((a) => a.startsWith(`--${k}=`)) || `=${d}`).split('=')[1];
const platform = arg('platform', process.platform);
const arch = arg('arch', process.arch);
const tag = arg('tag', `v${require('./package.json').version}`);
const short = { win32: 'win', darwin: 'mac', linux: 'linux' }[platform];
const name = 'HoneyComb Pet';

// 只放运行时文件（含 package.json）；测试、README、icon 源、dist 一律不进包
const KEEP = /^\/((main|preload|mini-preload|assets-download|dialog-core|float-core|download-server|pack|pet-event-server|pet-onboarding|pet-pick)\.js|index\.html|placeholder\.svg|manifest\.json|mood-map\.json|tray\.png|package\.json)$/;

(async () => {
  const out = path.join(__dirname, 'dist');
  fs.mkdirSync(out, { recursive: true });
  const [dir] = await packager({
    dir: __dirname,
    out,
    name,
    platform,
    arch,
    overwrite: true,
    prune: true,
    icon: platform === 'linux' ? undefined : path.join(__dirname, 'icon', 'icon'),
    ignore: (p) => p !== '' && !KEEP.test(p),
  });
  const zip = path.join(out, `honeycomb-pet-${tag}-${short}-${arch}.zip`);
  fs.rmSync(zip, { force: true });
  const base = path.basename(dir);
  const opts = { cwd: out, stdio: 'inherit' };
  if (process.platform === 'darwin') {
    execFileSync('ditto', ['-c', '-k', '--keepParent', path.join(base, `${name}.app`), zip], opts);
  } else if (process.platform === 'win32') {
    execFileSync('tar', ['-a', '-c', '-f', zip, base], opts);
  } else {
    execFileSync('zip', ['-qry', zip, base], opts);
  }
  console.log(zip);
})().catch((e) => { console.error(e); process.exit(1); });
