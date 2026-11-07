// HoneyComb 桌宠主进程：一个透明置顶窗（宠物 + 浮窗），托盘菜单，收 PetEvent，后台下素材。
// 点穿思路参考 dsh-pet（MIT，https://github.com/PC2005-cloud/dsh-pet）：窗口缺省忽略鼠标，命中后再接收。
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, nativeImage, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createFloat } = require('./float-core');
const { createPetEventServer, loadOrCreateToken, PORT, HOST } = require('./pet-event-server');
const { loadPack, listPacks, pickClip } = require('./pack');
const { downloadAll } = require('./assets-download');

if (process.platform === 'linux') app.commandLine.appendSwitch('ozone-platform', 'x11'); // 本版只支持 X11 / XWayland
if (!app.requestSingleInstanceLock()) app.quit();

const MARGIN = 16, BUBBLE_ROOM = 80, PANEL_W = 380, PANEL_H = 560;
const SIZES = { 小: 140, 中: 200, 大: 280 };
const dataDir = () => app.getPath('userData');
const petsDir = () => path.join(dataDir(), 'pets');

function sameOrigin(a, b) {
  try { return new URL(a).origin === new URL(b).origin; } catch { return false; }
}

const DEFAULTS = { server: 'http://127.0.0.1:8800', deviceToken: '', pet: 'deepseek', size: 200, autostart: false, downloadAssets: true };
let config = { ...DEFAULTS };
const configFile = () => path.join(dataDir(), 'config.json');
function loadConfig() { try { config = { ...DEFAULTS, ...JSON.parse(fs.readFileSync(configFile(), 'utf8')) }; } catch { /* 用缺省 */ } }
function saveConfig() { fs.mkdirSync(dataDir(), { recursive: true }); fs.writeFileSync(configFile(), JSON.stringify(config, null, 2) + '\n', { mode: 0o600 }); }

let win = null, tray = null, float = null, panelOpen = false;
let pack = null, mood = 'idle', lastClip = null, cursorTimer = null, lastCursor = '';

function send(ch, payload) { if (win && !win.isDestroyed()) win.webContents.send(ch, payload); }

function placeWindow() {
  const wa = screen.getPrimaryDisplay().workArea, s = config.size;
  const w = s + (panelOpen ? PANEL_W : 0), h = panelOpen ? Math.max(s + BUBBLE_ROOM, PANEL_H) : s + BUBBLE_ROOM;
  win.setBounds({ x: wa.x + wa.width - w - MARGIN, y: wa.y + wa.height - h - MARGIN, width: w, height: h });
}

function clipUrl(clip) { return clip && pack ? pathToFileURL(path.join(pack.dir, clip)).href : null; }
function play(m) {
  mood = m;
  const clip = pack ? pickClip(pack, m, lastClip) : null;
  lastClip = clip;
  send('play', { src: clipUrl(clip) });
}
function loadSelectedPack() { pack = loadPack(petsDir(), config.pet); play(mood); buildTrayMenu(); }

function onPetEvent(e) {
  if (e.text) send('bubble', e.text);
  play(e.mood);
}

function buildWindow() {
  win = new BrowserWindow({
    width: config.size, height: config.size + BUBBLE_ROOM, transparent: true, frame: false, alwaysOnTop: true,
    skipTaskbar: true, resizable: false, hasShadow: false, show: false, focusable: process.platform !== 'darwin',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, webviewTag: true, sandbox: true },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(true);
  placeWindow();
  win.loadFile('index.html');
  win.once('ready-to-show', () => {
    win.showInactive();
    send('size', config.size); play('idle');
  });
  // 命中测试要整窗光标：Linux 的忽略模式不转发鼠标移动，所以统一轮询光标位置交给前端判断。
  cursorTimer = setInterval(() => {
    if (!win || win.isDestroyed()) return;
    const p = screen.getCursorScreenPoint(), b = win.getBounds();
    const inside = p.x >= b.x && p.y >= b.y && p.x < b.x + b.width && p.y < b.y + b.height;
    const msg = inside ? { x: p.x - b.x, y: p.y - b.y } : null, key = JSON.stringify(msg);
    if (key !== lastCursor) { lastCursor = key; send('cursor', msg); }
  }, 50);
}

function startFloat() {
  const server = config.server.replace(/\/+$/, '');
  try {
    const u = new URL(server);
    if (u.protocol !== 'https:' && !['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname)) console.warn(`服务器 ${u.origin} 不是 https，设备令牌会明文发送`);
  } catch { /* 地址不合法：下面的请求会失败并熄灭红点 */ }
  float = createFloat({
    timers: { setTimeout, clearTimeout },
    miniUrl: `${server}/hive/mini`,
    async fetchLiveAsk() {
      const r = await fetch(`${server}/api/core/activity/live-ask`, {
        headers: { Authorization: `Bearer ${config.deviceToken}` }, signal: AbortSignal.timeout(8000),
      });
      if (!r.ok) throw new Error(`live-ask ${r.status}`);
      return (await r.json()).ask || null;
    },
    // 设备令牌只有 read 范围；拖放由 /hive/mini 页面用人的登录会话完成，这里不代发。
    assign: async () => ({ status: 403 }),
    onBadge: on => send('badge', on),
    onCard: state => send('card', state),
    onPanel: ({ open, url }) => {
      panelOpen = open;
      if (process.platform === 'darwin') win.setFocusable(open);
      placeWindow();
      send('panel', { open, url, miniPreload: pathToFileURL(path.join(__dirname, 'mini-preload.js')).href });
    },
  });
  if (config.deviceToken) float.start(); // 没配设备令牌就不轮询，红点不亮
}

function startAssetDownload() {
  if (!config.downloadAssets) return;
  const manifest = require('./manifest.json'), moodMap = require('./mood-map.json');
  const roles = Object.fromEntries(Object.entries(manifest.roles).sort(([a], [b]) => (b === config.pet) - (a === config.pet)));
  downloadAll({
    manifest: { ...manifest, roles }, moodMap, destDir: petsDir(),
    onProgress: (id, err) => { if (err) console.error('下载失败', id, err.message); },
    onRoleDone: kind => { if (kind === config.pet && !pack) loadSelectedPack(); else buildTrayMenu(); },
  }).then(({ failed }) => {
    if (failed.length) console.error(`素材有 ${failed.length} 个文件没下完，下次启动继续`);
    if (!pack) loadSelectedPack(); else buildTrayMenu();
  }).catch(e => console.error('素材下载出错', e));
}

function buildTrayMenu() {
  if (!tray) return;
  const packs = listPacks(petsDir());
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '换宠物', submenu: [
      { label: '内置形象', type: 'radio', checked: !pack, click: () => { config.pet = ''; saveConfig(); loadSelectedPack(); } },
      ...packs.map(p => ({ label: p.name || p.kind, type: 'radio', checked: !!pack && pack.kind === p.kind, click: () => { config.pet = p.kind; saveConfig(); loadSelectedPack(); } })),
    ] },
    { label: '大小', submenu: Object.entries(SIZES).map(([label, px]) => ({
      label, type: 'radio', checked: config.size === px,
      click: () => { config.size = px; saveConfig(); placeWindow(); send('size', px); },
    })) },
    ...(process.platform === 'linux' ? [] : [{
      label: '开机自启', type: 'checkbox', checked: config.autostart,
      click: item => { config.autostart = item.checked; saveConfig(); app.setLoginItemSettings({ openAtLogin: item.checked }); },
    }]),
    { type: 'separator' },
    { label: '退出', click: () => app.quit() },
  ]));
}

app.whenReady().then(() => {
  // 嵌入页是远程网页：这个分区的权限请求（摄像头、通知、定位……）一律拒绝。
  session.fromPartition('persist:hive-mini').setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  if (process.platform === 'darwin') app.dock.hide();
  loadConfig();
  const token = loadOrCreateToken(dataDir());
  createPetEventServer({ token, onEvent: onPetEvent })
    .on('error', e => console.error(`监听 ${HOST}:${PORT} 失败：${e.message}`))
    .listen(PORT, HOST);

  // 浮窗里的网页只许用我们给的分区与预加载脚本，不开节点集成，不弹新窗。
  app.on('web-contents-created', (_e, wc) => {
    wc.setWindowOpenHandler(() => ({ action: 'deny' }));
    wc.on('will-attach-webview', (ev, prefs, params) => {
      // 只许嵌我们配置的服务器上的 /hive/mini，预加载脚本只能是我们自己的。
      const p = sameOrigin(params.src, config.server) ? new URL(params.src).pathname : '';
      if (p !== '/hive/mini' && !p.startsWith('/hive/mini/')) { ev.preventDefault(); return; }
      prefs.preload = path.join(__dirname, 'mini-preload.js');
      prefs.nodeIntegration = false; prefs.contextIsolation = true; prefs.sandbox = true;
      prefs.partition = 'persist:hive-mini';
    });
    // 嵌入页只能在本服务器内跳转（登录页也在同一服务器上）。
    const stayHome = (ev, url) => { if (wc.getType() === 'webview' && !sameOrigin(url, config.server)) ev.preventDefault(); };
    wc.on('will-navigate', stayHome);
    wc.on('will-redirect', stayHome);
    wc.on('will-frame-navigate', (ev) => stayHome(ev, ev.url));
  });

  buildWindow();
  tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'tray.png')));
  tray.setToolTip('HoneyComb 桌宠');
  loadSelectedPack();
  startFloat();
  startAssetDownload();

  ipcMain.on('hit', (_e, hit) => { if (win) win.setIgnoreMouseEvents(!hit); });
  ipcMain.on('hover', (_e, on) => { if (float) on ? float.hoverEnter() : float.hoverLeave(); });
  ipcMain.on('page-done', () => { if (float) float.pageDone(); });
  ipcMain.on('clip-ended', () => play(mood === 'happy' || mood === 'worried' ? 'idle' : mood));
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { clearInterval(cursorTimer); if (float) float.stop(); });
