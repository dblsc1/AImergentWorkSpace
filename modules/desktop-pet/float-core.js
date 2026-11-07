// 浮窗核心（desktop.pet.v1「四」）：红点轮询、面板展开 / 收起、assign 409 重试。与 Electron 无关。
// 设备令牌只在调用方注入的 fetchLiveAsk 闭包里，这里碰不到。

const POLL_MS = 10000;
const COLLAPSE_MS = 800;
const CLOSE_AFTER_DONE_MS = 2000;
const CLOSE_AFTER_LEFT_MS = 2000;
const MAX_FAILS = 3;

// 红点贴桌宠窗口右上角；取不到桌宠位置就贴屏幕右下角。
function badgePos(petRect, screen, size = 16) {
  if (!petRect) return { x: screen.width - size, y: screen.height - size };
  return { x: petRect.x + petRect.width - size, y: petRect.y };
}

// fetchLiveAsk() -> ask 对象或 null，失败 / 401 抛错；assign(body) -> {status}
function createFloat({ fetchLiveAsk, assign, timers, miniUrl, onBadge = () => {}, onPanel = () => {}, onCard = () => {},
  pollMs = POLL_MS, collapseMs = COLLAPSE_MS }) {
  const s = { badge: false, open: false, card: 'ask', ask: null, fails: 0 };
  let pollTimer = null, collapseTimer = null, closeTimer = null, running = false;

  const setBadge = on => { if (s.badge !== on) { s.badge = on; onBadge(on); } };
  const setCard = c => { if (s.card !== c) { s.card = c; onCard(c); } };
  const clear = t => { if (t) timers.clearTimeout(t); return null; };
  const open = () => { if (!s.open) { s.open = true; setCard('ask'); onPanel({ open: true, url: miniUrl }); } };
  const close = () => {
    collapseTimer = clear(collapseTimer); closeTimer = clear(closeTimer);
    if (s.open) { s.open = false; onPanel({ open: false, url: null }); }
  };
  const closeLater = ms => { closeTimer = clear(closeTimer); closeTimer = timers.setTimeout(() => { closeTimer = null; close(); }, ms); };

  async function poll() {
    try {
      const ask = await fetchLiveAsk();
      s.fails = 0;
      const had = s.ask;
      s.ask = ask || null;
      setBadge(!!s.ask);
      if (had && !s.ask && s.open) { setCard('left'); closeLater(CLOSE_AFTER_LEFT_MS); }
    } catch {
      if (++s.fails >= MAX_FAILS) setBadge(false);
    }
  }
  function loop() {
    if (!running) return;
    poll().finally(() => { if (running) pollTimer = timers.setTimeout(loop, pollMs); });
  }

  async function assignTo(projectId) {
    const body = ask => ({ date: ask.date, blockKey: ask.blockKey, ifSeq: ask.seq, projectId, remember: true });
    if (!s.ask) return;
    let r = await assign(body(s.ask));
    if (r.status === 409) {
      let fresh = null;
      try { fresh = await fetchLiveAsk(); } catch { /* 取不到就当没了 */ }
      if (fresh && fresh.blockKey === s.ask.blockKey) { s.ask = fresh; r = await assign(body(fresh)); }
      else { s.ask = fresh || null; setBadge(!!s.ask); return close(); }
    }
    if (r.status >= 200 && r.status < 300) return pageDone();
    if (r.status === 409) return close();
  }
  function pageDone() { s.ask = null; setBadge(false); closeLater(CLOSE_AFTER_DONE_MS); }

  return {
    start() { running = true; loop(); },
    stop() { running = false; pollTimer = clear(pollTimer); close(); },
    hoverEnter() { collapseTimer = clear(collapseTimer); if (s.open || s.ask) open(); },
    hoverLeave() { if (!s.open) return; collapseTimer = clear(collapseTimer); collapseTimer = timers.setTimeout(() => { collapseTimer = null; close(); }, collapseMs); },
    pageDone, assignTo,
    state: () => ({ badge: s.badge, open: s.open, card: s.card, ask: s.ask }),
  };
}

module.exports = { createFloat, badgePos, POLL_MS, COLLAPSE_MS };
