// 桌宠新手引导（desktop.pet.v1「三·4」）：服务端还没分区就开口请用户去蜂巢建；分区 + 项目 + 任务齐了，说几句蜂巢怎么看。与 Electron 无关。
const OPEN = { id: 'open', label: '打开蜂巢' };
const ASK_TEXT = '先去蜂巢建一个分区，再建项目和任务吧。';
const INTRO = [
  '左边是你的 AI 代理，右上角是你的时钟。',
  '左下角是还没归类的窗口托盘。',
  '把时间元素拖到格子上，就记进那个任务。',
];

// tree = views/tree 的返回
const hasZone = tree => Array.isArray(tree?.zones) && tree.zones.length > 0;
const hasTask = tree => Array.isArray(tree?.projects) && tree.projects.some(p => Array.isArray(p.tasks) && p.tasks.length > 0);
const isReady = tree => hasZone(tree) && hasTask(tree);

// readTree() → tree，失败抛错；say(msg) → Promise；withdraw(tag)；openHive()；isDone() / markDone()
function createPetOnboarding({ readTree, say, withdraw, openHive, isDone, markDone, timers, pollMs = 10000 }) {
  let timer = null, running = false, asked = false;

  const stop = () => { running = false; if (timer) timers.clearTimeout(timer); timer = null; };

  async function intro() {
    for (let i = 0; i < INTRO.length; i++) {
      const last = i === INTRO.length - 1;
      const r = await say({ text: INTRO[i], buttons: [last ? { id: 'ok', label: '知道了' } : { id: 'next', label: '下一句' }], tag: 'intro' });
      if (r === null) break;
    }
  }
  async function ask() {
    asked = true;
    if (await say({ text: ASK_TEXT, buttons: [OPEN], tag: 'ask' }) === OPEN.id) openHive();
  }

  async function tick() {
    let tree;
    try { tree = await readTree(); } catch { return; }
    if (!asked) {
      if (hasZone(tree)) { markDone(); return stop(); }   // 老账号：不说话
      ask();
      return;
    }
    if (isReady(tree)) {
      stop(); markDone(); withdraw('ask');
      await intro();
    }
  }
  function loop() {
    if (!running) return;
    tick().finally(() => { if (running) timer = timers.setTimeout(loop, pollMs); });
  }

  return {
    start() { if (running || isDone()) return; running = true; loop(); },
    stop,
    replay: intro,
  };
}

module.exports = { createPetOnboarding, isReady, ASK_TEXT, INTRO };
