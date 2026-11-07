const test = require('node:test');
const assert = require('node:assert');
const { pickPackKind, watchedAgent } = require('./pet-pick');
const { DEFAULT_PACKS_BY_TOOL } = require('./pack');

const run = (over = {}) => ({ runId: 'run_1', taskId: null, agent: 'garden', tool: 'codex', model: null, startedAt: '2026-11-08T10:00:00+08:00', phase: 'working', label: 'garden 写作', elapsedSeconds: 60, ...over });
const focus = (over = {}) => ({ state: 'present', app: 'code', title: 'garden 写作', since: '2026-11-08T10:01:00+08:00', ...over });
const view = (agents, f) => ({ running: false, agents, focus: f });
const MAP = { 'claude-code': 'claude', codex: 'gpt', 'gemini-cli': 'gemini', '*': 'deepseek' };

test('人在看代理的窗口：用那条运行的 tool 查对照', () => {
  assert.equal(pickPackKind({ current: view([run({ tool: 'codex' })], focus()), packsByTool: MAP, configPet: 'claude' }), 'gpt');
  assert.equal(pickPackKind({ current: view([run({ tool: 'claude-code' })], focus()), packsByTool: MAP, configPet: 'gpt' }), 'claude');
});

test('标题比对忽略大小写与多余空白', () => {
  const cur = view([run({ tool: 'gemini-cli', label: 'Garden  写作' })], focus({ title: '  garden 写作 ' }));
  assert.equal(pickPackKind({ current: cur, packsByTool: MAP, configPet: 'claude' }), 'gemini');
});

test('查不到的 tool 用 *', () => {
  assert.equal(pickPackKind({ current: view([run({ tool: 'aider' })], focus()), packsByTool: MAP, configPet: 'claude' }), 'deepseek');
});

test('对照没有 * 时用 pack.js 的缺省 *', () => {
  assert.equal(pickPackKind({ current: view([run({ tool: 'aider' })], focus()), packsByTool: { codex: 'gpt' }, configPet: 'claude' }), 'deepseek');
});

test('对照的值不是非空字符串、或键是原型上的名字，都不算查到', () => {
  assert.equal(pickPackKind({ current: view([run({ tool: 'codex' })], focus()), packsByTool: { codex: '', '*': 'dsh' }, configPet: 'claude' }), 'dsh');
  assert.equal(pickPackKind({ current: view([run({ tool: 'constructor' })], focus()), packsByTool: { '*': 'dsh' }, configPet: 'claude' }), 'dsh');
});

test('服务端对照读不到（null，如 403）用 pack.js 的缺省对照', () => {
  assert.equal(pickPackKind({ current: view([run({ tool: 'claude-code' })], focus()), packsByTool: null, configPet: 'gpt' }), DEFAULT_PACKS_BY_TOOL['claude-code']);
  assert.equal(pickPackKind({ current: view([run({ tool: 'nope' })], focus()), configPet: 'gpt' }), 'deepseek');
});

test('人没在看代理：用托盘选的默认包（含内置形象的空串）', () => {
  const agents = [run({ tool: 'codex' })];
  assert.equal(pickPackKind({ current: view(agents, focus({ title: '别的窗口' })), packsByTool: MAP, configPet: 'claude' }), 'claude');
  assert.equal(pickPackKind({ current: view(agents, focus({ title: '别的窗口' })), packsByTool: MAP, configPet: '' }), '');
  assert.equal(pickPackKind({ current: null, packsByTool: MAP, configPet: 'claude' }), 'claude');
});

test('离开电脑（afk）或没有 focus：用默认包', () => {
  const agents = [run({ tool: 'codex' })];
  assert.equal(pickPackKind({ current: view(agents, focus({ state: 'afk' })), packsByTool: MAP, configPet: 'claude' }), 'claude');
  assert.equal(pickPackKind({ current: view(agents, null), packsByTool: MAP, configPet: 'claude' }), 'claude');
});

test('标题短于 3 个字符不认作代理窗口', () => {
  const cur = view([run({ label: 'ab' })], focus({ title: 'ab' }));
  assert.equal(watchedAgent(cur), null);
  assert.equal(pickPackKind({ current: cur, packsByTool: MAP, configPet: 'claude' }), 'claude');
});

test('焦点窗口对不上任何运行的 label：不算在看代理', () => {
  const cur = view([run({ label: null })], focus({ title: 'garden 写作' }));
  assert.equal(watchedAgent(cur), null);
});
