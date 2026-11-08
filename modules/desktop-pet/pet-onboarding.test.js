const test = require('node:test');
const assert = require('node:assert');
const { createPetOnboarding, isReady, INTRO } = require('./pet-onboarding');

const EMPTY = { zones: [], projects: [] };
const ZONE = { zones: [{ id: 'z' }], projects: [] };
const FULL = { zones: [{ id: 'z' }], projects: [{ id: 'p', tasks: [{ id: 't' }] }] };

// 假时钟：tickAll() 触发下一个定时器
function make({ trees, done = false, answers = [] }) {
  const said = [], opened = []; let marked = 0, i = 0, withdrawn = [], pending = null;
  const timers = { setTimeout: fn => { pending = fn; return 1; }, clearTimeout: () => { pending = null; } };
  const o = createPetOnboarding({
    readTree: async () => { const t = trees[Math.min(i++, trees.length - 1)]; if (t instanceof Error) throw t; return t; },
    say: async m => { said.push(m); return answers.length ? answers.shift() : m.buttons[0].id; },
    withdraw: t => withdrawn.push(t),
    openHive: () => opened.push(1), isDone: () => done, markDone: () => { marked++; }, timers,
  });
  const flush = () => new Promise(r => setImmediate(r));
  const fire = async () => { const f = pending; pending = null; if (f) f(); await flush(); await flush(); };
  return { o, said, opened, marked: () => marked, withdrawn, fire, hasTimer: () => !!pending, flush };
}

test('isReady：要有分区且某个项目有任务', () => {
  assert.equal(isReady(EMPTY), false); assert.equal(isReady(ZONE), false); assert.equal(isReady(FULL), true);
  assert.equal(isReady(null), false);
});

test('已经做过：start 什么都不做', async () => {
  const t = make({ trees: [EMPTY], done: true });
  t.o.start(); await t.flush();
  assert.equal(t.said.length, 0); assert.equal(t.hasTimer(), false);
});

test('没分区：说一句只说一次；点「打开蜂巢」开浏览器；每轮继续轮询', async () => {
  const t = make({ trees: [EMPTY, EMPTY, EMPTY] });
  t.o.start(); await t.flush(); await t.flush();
  assert.equal(t.said.length, 1); assert.equal(t.said[0].buttons[0].label, '打开蜂巢');
  assert.equal(t.opened.length, 1);
  await t.fire(); await t.fire();
  assert.equal(t.said.length, 1, '不重复说');
  assert.equal(t.hasTimer(), true);
});

test('分区 + 项目 + 任务齐了：收回提问、说 3 句（下一句 / 下一句 / 知道了）、记完成、停止轮询', async () => {
  const t = make({ trees: [EMPTY, ZONE, FULL] });
  t.o.start(); await t.flush(); await t.flush();
  await t.fire();
  assert.equal(t.said.length, 1, '只有分区还不够');
  await t.fire();
  assert.deepEqual(t.said.slice(1).map(m => m.text), INTRO);
  assert.deepEqual(t.said.slice(1).map(m => m.buttons[0].label), ['下一句', '下一句', '知道了']);
  assert.equal(t.marked(), 1); assert.deepEqual(t.withdrawn, ['ask']);
  assert.equal(t.hasTimer(), false);
});

test('介绍中途点 ×：不再说后面的', async () => {
  const t = make({ trees: [EMPTY, FULL], answers: [null, 'next', null] });
  t.o.start(); await t.flush(); await t.flush();
  await t.fire();
  assert.equal(t.said.length, 3);   // 提问 + 两句
});

test('老账号（启动时就有分区）：不说话，直接记完成', async () => {
  const t = make({ trees: [ZONE] });
  t.o.start(); await t.flush(); await t.flush();
  assert.equal(t.said.length, 0); assert.equal(t.marked(), 1); assert.equal(t.hasTimer(), false);
});

test('读树失败：不说话、不记完成，下一轮再试', async () => {
  const t = make({ trees: [new Error('403'), EMPTY] });
  t.o.start(); await t.flush(); await t.flush();
  assert.equal(t.said.length, 0); assert.equal(t.marked(), 0); assert.equal(t.hasTimer(), true);
  await t.fire();
  assert.equal(t.said.length, 1);
});

test('replay：不管完成与否，直接说一遍介绍', async () => {
  const t = make({ trees: [FULL], done: true });
  await t.o.replay();
  assert.equal(t.said.length, 3); assert.equal(t.marked(), 0);
});
