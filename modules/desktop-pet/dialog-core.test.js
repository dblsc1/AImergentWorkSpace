const test = require('node:test');
const assert = require('node:assert');
const { createDialogQueue, normalizeMessage } = require('./dialog-core');

function make() {
  const shown = []; let hidden = 0;
  const q = createDialogQueue({ show: p => shown.push(p), hide: () => { hidden++; } });
  return { q, shown, hidden: () => hidden };
}
const btn = (id, label = id) => ({ id, label });

test('一条消息：显示，点按钮 → 回按钮 id，队列空了收起', async () => {
  const { q, shown, hidden } = make();
  const p = q.say({ text: '你好', buttons: [btn('a', '好'), btn('b', '不')] });
  assert.equal(shown.length, 1);
  assert.deepEqual(shown[0].buttons, [btn('a', '好'), btn('b', '不')]);
  assert.equal(q.answer(shown[0].seq, { id: 'b' }), true);
  assert.equal(await p, 'b');
  assert.equal(hidden(), 1);
  assert.equal(q.isOpen(), false);
});

test('排队：先进先出，前一条答完才显示下一条，中间不收起', async () => {
  const { q, shown, hidden } = make();
  const p1 = q.say({ text: '一', buttons: [btn('x')] }), p2 = q.say({ text: '二', buttons: [btn('y')] });
  assert.equal(shown.length, 1);
  q.answer(shown[0].seq, { id: 'x' });
  assert.equal(await p1, 'x');
  assert.equal(shown.length, 2); assert.equal(shown[1].text, '二'); assert.equal(hidden(), 0);
  q.answer(shown[1].seq, null);
  assert.equal(await p2, null);
  assert.equal(hidden(), 1);
});

test('按钮最多 3 个；缺 id / label 的丢掉；文字空 → 直接 null 不显示', async () => {
  const { q, shown } = make();
  q.say({ text: 't', buttons: [btn('1'), btn('2'), btn('3'), btn('4')] });
  assert.deepEqual(shown[0].buttons.map(b => b.id), ['1', '2', '3']);
  assert.equal(normalizeMessage({ text: 't', buttons: [{ id: '', label: 'x' }, { id: 'a' }, null] }).buttons.length, 0);
  assert.equal(await q.say({ text: '  ' }), null);
  assert.equal(await q.say(null), null);
  assert.equal(shown.length, 1);
});

test('输入框：回文字；没有输入框或空文字的答案忽略；过期 seq 忽略', async () => {
  const { q, shown } = make();
  const p = q.say({ text: '叫什么', input: { placeholder: '名字' } });
  assert.equal(shown[0].input.placeholder, '名字');
  assert.equal(q.answer(shown[0].seq, { text: '   ' }), false);
  assert.equal(q.answer(shown[0].seq + 9, { text: 'x' }), false);
  assert.equal(q.answer(shown[0].seq, { id: 'nope' }), false);
  assert.equal(q.answer(shown[0].seq, { text: ' 小蜂 ' }), true);
  assert.equal(await p, '小蜂');
  const p2 = q.say({ text: '无输入框', buttons: [btn('a')] });
  assert.equal(q.answer(shown[1].seq, { text: 'x' }), false);
  q.answer(shown[1].seq, null); await p2;
});

test('撤回 tag：排队的和显示中的都回 null，其他 tag 不动', async () => {
  const { q, shown } = make();
  const a = q.say({ text: 'A', tag: 'ask' }), b = q.say({ text: 'B', tag: 'other' }), c = q.say({ text: 'C', tag: 'ask' });
  q.withdraw('ask');
  assert.equal(await a, null); assert.equal(await c, null);
  assert.equal(shown.at(-1).text, 'B');
  q.answer(shown.at(-1).seq, null);
  assert.equal(await b, null);
});
