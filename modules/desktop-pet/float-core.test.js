const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const { createFloat, badgePos } = require('./float-core');

function fakeClock() {
  let now = 0, id = 0, q = [];
  return {
    setTimeout(fn, ms) { q.push({ id: ++id, at: now + ms, fn }); return id; },
    clearTimeout(i) { q = q.filter(t => t.id !== i); },
    async tick(ms) {
      const end = now + ms;
      for (let i = 0; i < 10; i++) await Promise.resolve();
      for (;;) {
        q.sort((a, b) => a.at - b.at);
        if (!q.length || q[0].at > end) break;
        const t = q.shift(); now = t.at; t.fn();
        for (let i = 0; i < 10; i++) await Promise.resolve();
      }
      now = end;
    },
  };
}
const ask = (seq = 3, blockKey = 'b1') => ({ displayName: 'figma.com', focusedSeconds: 372, date: '2026-11-07', seq, blockKey });

function setup(replies, extra = {}) {
  const clock = fakeClock(); const log = { fetch: 0, badge: [], panel: [], card: [], assign: [] };
  const queue = [...replies];
  const f = createFloat({
    timers: clock, miniUrl: 'http://srv/hive/mini',
    fetchLiveAsk: async () => { log.fetch++; const r = queue.length > 1 ? queue.shift() : queue[0]; if (r instanceof Error) throw r; return r; },
    assign: async (a) => { log.assign.push(a); return extra.assignReplies ? extra.assignReplies.shift() : { status: 200 }; },
    onBadge: on => log.badge.push(on), onPanel: p => log.panel.push(p), onCard: c => log.card.push(c),
  });
  return { clock, log, f };
}

test('FW-F01 每 10 秒请求一次', async () => {
  const { clock, log, f } = setup([null]);
  f.start(); await clock.tick(0); assert.equal(log.fetch, 1);
  await clock.tick(9999); assert.equal(log.fetch, 1);
  await clock.tick(1); assert.equal(log.fetch, 2);
  await clock.tick(10000); assert.equal(log.fetch, 3);
});

test('FW-F02/F03 红点随 ask 亮灭', async () => {
  const { clock, log, f } = setup([ask(), null]);
  f.start(); await clock.tick(0); assert.equal(f.state().badge, true);
  await clock.tick(10000); assert.equal(f.state().badge, false);
  assert.deepEqual(log.badge, [true, false]);
});

test('FW-F04 失败保持原状，连续 3 轮失败熄灭，成功清零', async () => {
  const E = new Error('401');
  const { clock, f } = setup([ask(), E, E, E, ask(), E, E, E]);
  f.start(); await clock.tick(0); assert.equal(f.state().badge, true);
  await clock.tick(10000); assert.equal(f.state().badge, true);
  await clock.tick(10000); assert.equal(f.state().badge, true);
  await clock.tick(10000); assert.equal(f.state().badge, false);
  await clock.tick(10000); assert.equal(f.state().badge, true);
  await clock.tick(30000); assert.equal(f.state().badge, false);
});

test('FW-F05/F06 badgePos', () => {
  assert.deepEqual(badgePos({ x: 100, y: 50, width: 200, height: 200 }, { width: 1920, height: 1080 }, 16), { x: 284, y: 50 });
  assert.deepEqual(badgePos(null, { width: 1920, height: 1080 }, 16), { x: 1904, y: 1064 });
});

test('FW-F07/F08 悬停弹出 /hive/mini，URL 不含令牌；核心不持有令牌', async () => {
  const { clock, log, f } = setup([ask()]);
  f.start(); await clock.tick(0); f.hoverEnter();
  assert.deepEqual(log.panel.at(-1), { open: true, url: 'http://srv/hive/mini' });
  assert.ok(!/token|Bearer|Authorization/i.test(fs.readFileSync(`${__dirname}/float-core.js`, 'utf8')));
});

test('FW-F09 ask:null 时悬停不弹', async () => {
  const { clock, log, f } = setup([null]);
  f.start(); await clock.tick(0); f.hoverEnter();
  assert.equal(log.panel.length, 0);
});

test('悬停移开 800ms 收起，期间回来取消', async () => {
  const { clock, log, f } = setup([ask()]);
  f.start(); await clock.tick(0); f.hoverEnter(); f.hoverLeave();
  await clock.tick(799); assert.equal(f.state().open, true);
  f.hoverEnter(); await clock.tick(2000); assert.equal(f.state().open, true);
  f.hoverLeave(); await clock.tick(800); assert.equal(f.state().open, false);
  assert.deepEqual(log.panel.at(-1), { open: false, url: null });
});

test('FW-F10 完成消息：红点灭，2000ms 后收起', async () => {
  const { clock, f } = setup([ask()]);
  f.start(); await clock.tick(0); f.hoverEnter(); f.pageDone();
  assert.equal(f.state().badge, false);
  await clock.tick(1999); assert.equal(f.state().open, true);
  await clock.tick(1); assert.equal(f.state().open, false);
});

test('面板开着 ask 变 null：显示已离开，2 秒后收起', async () => {
  const { clock, log, f } = setup([ask(), null]);
  f.start(); await clock.tick(0); f.hoverEnter();
  await clock.tick(10000);
  assert.equal(f.state().card, 'left'); assert.equal(log.card.at(-1), 'left');
  await clock.tick(1999); assert.equal(f.state().open, true);
  await clock.tick(1); assert.equal(f.state().open, false);
});

test('assign 409：重取 live-ask，块还在用新 seq 重试一次', async () => {
  const { clock, log, f } = setup([ask(3), ask(4)], { assignReplies: [{ status: 409 }, { status: 200 }] });
  f.start(); await clock.tick(0); f.hoverEnter();
  await f.assignTo('p1');
  assert.equal(log.assign.length, 2);
  assert.deepEqual(log.assign[0], { date: '2026-11-07', blockKey: 'b1', ifSeq: 3, projectId: 'p1', remember: true });
  assert.equal(log.assign[1].ifSeq, 4);
  assert.equal(f.state().badge, false);
});

test('assign 409：块没了就收起，不重试；再 409 不再重试', async () => {
  let r = setup([ask(3), null], { assignReplies: [{ status: 409 }] });
  r.f.start(); await r.clock.tick(0); r.f.hoverEnter();
  await r.f.assignTo('p1');
  assert.equal(r.log.assign.length, 1); assert.equal(r.f.state().open, false);

  r = setup([ask(3), ask(4)], { assignReplies: [{ status: 409 }, { status: 409 }] });
  r.f.start(); await r.clock.tick(0); r.f.hoverEnter();
  await r.f.assignTo('p1');
  assert.equal(r.log.assign.length, 2); assert.equal(r.f.state().open, false);
});

test('FW-F11 只做三件事：没有宠物绘制 / 事件代码', () => {
  const src = fs.readFileSync(`${__dirname}/float-core.js`, 'utf8');
  assert.ok(!/PetEvent|<video|canvas|mood/i.test(src));
});
