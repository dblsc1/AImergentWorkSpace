// 对话框（desktop.pet.v1「三·3」）：消息排队，一次显示一条；与 Electron 无关。
// say({text, buttons:[{id,label}], input?:{placeholder}, tag?}) → Promise<按钮 id | 输入的文字 | null（点 × / 被撤回 / 消息不合格）>
const MAX_BUTTONS = 3, MAX_TEXT = 200, MAX_LABEL = 12;
const clip = (s, n) => [...s].slice(0, n).join('');

function normalizeMessage(m) {
  if (!m || typeof m.text !== 'string' || !m.text.trim()) return null;
  const buttons = (Array.isArray(m.buttons) ? m.buttons : [])
    .filter(b => b && typeof b.id === 'string' && b.id && typeof b.label === 'string' && b.label.trim())
    .slice(0, MAX_BUTTONS).map(b => ({ id: b.id, label: clip(b.label, MAX_LABEL) }));
  const input = m.input && typeof m.input === 'object' ? { placeholder: clip(String(m.input.placeholder ?? ''), 40) } : null;
  return { text: clip(m.text, MAX_TEXT), buttons, input, tag: typeof m.tag === 'string' ? m.tag : null };
}

// show(payload)：有新的一条要显示；hide()：队列空了。payload = {seq, text, buttons, input}
function createDialogQueue({ show, hide }) {
  let seq = 0, current = null;
  const pending = [];

  function advance() {
    current = pending.shift() || null;
    if (!current) return hide();
    show({ seq: current.seq, text: current.msg.text, buttons: current.msg.buttons, input: current.msg.input });
  }

  return {
    say(raw) {
      const msg = normalizeMessage(raw);
      if (!msg) return Promise.resolve(null);
      return new Promise(resolve => {
        pending.push({ seq: ++seq, msg, resolve });
        if (!current) advance();
      });
    },
    // 渲染进程回的答案：value = {id} | {text} | null；seq 对不上（过期）或内容不在这条消息里就忽略
    answer(s, value) {
      if (!current || current.seq !== s) return false;
      let result = null;
      if (value && typeof value.id === 'string') {
        if (!current.msg.buttons.some(b => b.id === value.id)) return false;
        result = value.id;
      } else if (value && typeof value.text === 'string') {
        const t = value.text.trim();
        if (!current.msg.input || !t) return false;
        result = clip(t, MAX_TEXT);
      }
      const done = current;
      done.resolve(result); advance();
      return true;
    },
    // 撤回某个 tag 的所有消息（显示中的也关掉，结果 null）
    withdraw(tag) {
      for (let i = pending.length - 1; i >= 0; i--) if (pending[i].msg.tag === tag) { pending[i].resolve(null); pending.splice(i, 1); }
      if (current && current.msg.tag === tag) { const done = current; done.resolve(null); advance(); }
    },
    isOpen: () => !!current,
  };
}

module.exports = { createDialogQueue, normalizeMessage, MAX_BUTTONS };
