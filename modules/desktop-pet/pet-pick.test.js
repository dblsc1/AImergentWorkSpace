const test = require('node:test');
const assert = require('node:assert');
const { pickPackKind, watchedAgent } = require('./pet-pick');
const { DEFAULT_PACKS_BY_TOOL } = require('./pack');

const NOW = '2026-11-08T10:00:20+08:00';
const agent = (over = {}) => ({ runId: 'run_1', agent: 'garden', tool: 'codex', model: null, label: 'garden 写作', taskId: null, projectId: null, startAt: '2026-11-08T09:50:00+08:00', endAt: null, outcome: null, elapsedSeconds: 600, overdue: false, lost: false, phases: [], attention: [], ...over });
const seg = (over = {}) => ({ deviceId: 'dev_1', from: '2026-11-08T10:00:00+08:00', to: '2026-11-08T10:00:15+08:00', app: 'code', title: 'garden 写作', afk: false, runId: 'run_1', ...over });
const lanes = (segments, agents = [agent()], now = NOW) => ({ now, human: { sessions: [], running: null, presence: segments }, agents, interactions: [], truncated: false });
const MAP = { 'claude-code': 'claude', codex: 'gpt', 'gemini-cli': 'gemini', '*': 'deepseek' };

test('最近一段人在电脑前、指向还在跑的运行：用那条运行的 tool 查对照', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg()]), packsByTool: MAP, configPet: 'claude' }), 'gpt');
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: 'claude-code' })]), packsByTool: MAP, configPet: 'gpt' }), 'claude');
});

test('取 to 最晚的那段，不看数组顺序', () => {
  const older = seg({ to: '2026-11-08T09:59:00+08:00', runId: 'run_old' });
  const newer = seg({ to: '2026-11-08T10:00:15+08:00', runId: 'run_1' });
  const agents = [agent({ runId: 'run_old', tool: 'claude-code' }), agent({ runId: 'run_1', tool: 'codex' })];
  assert.equal(pickPackKind({ lanes: lanes([newer, older], agents), packsByTool: MAP, configPet: 'claude' }), 'gpt');
});

test('同名的多条运行：按 runId 取，不靠标题', () => {
  const agents = [agent({ runId: 'run_a', tool: 'claude-code' }), agent({ runId: 'run_b', tool: 'gemini-cli' })];
  assert.equal(pickPackKind({ lanes: lanes([seg({ runId: 'run_b' })], agents), packsByTool: MAP, configPet: 'claude' }), 'gemini');
});

test('查不到的 tool 用 *；tool 为 null 也用 *', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: 'aider' })]), packsByTool: MAP, configPet: 'claude' }), 'deepseek');
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: null })]), packsByTool: MAP, configPet: 'claude' }), 'deepseek');
});

test('对照没有 * 时用 pack.js 的缺省 *', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: 'aider' })]), packsByTool: { codex: 'gpt' }, configPet: 'claude' }), 'deepseek');
});

test('对照的值不是非空字符串、或键是原型上的名字，都不算查到', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg()]), packsByTool: { codex: '', '*': 'dsh' }, configPet: 'claude' }), 'dsh');
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: 'constructor' })]), packsByTool: { '*': 'dsh' }, configPet: 'claude' }), 'dsh');
});

test('服务端对照读不到（null，如 403）用 pack.js 的缺省对照', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: 'claude-code' })]), packsByTool: null, configPet: 'gpt' }), DEFAULT_PACKS_BY_TOOL['claude-code']);
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ tool: 'nope' })]), configPet: 'gpt' }), 'deepseek');
});

test('没有在看代理：用托盘选的默认包（含内置形象的空串）', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg({ runId: null })]), packsByTool: MAP, configPet: 'claude' }), 'claude');
  assert.equal(pickPackKind({ lanes: lanes([seg({ runId: null })]), packsByTool: MAP, configPet: '' }), '');
  assert.equal(pickPackKind({ lanes: null, packsByTool: MAP, configPet: 'claude' }), 'claude');
  assert.equal(pickPackKind({ lanes: lanes([]), packsByTool: MAP, configPet: 'claude' }), 'claude');
});

test('最近一段是离开（afk）：用默认包', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg({ afk: true })]), packsByTool: MAP, configPet: 'claude' }), 'claude');
});

test('最后一段结束超过 15 秒：不算在看（15 秒整仍算）', () => {
  const stale = seg({ to: '2026-11-08T10:00:04+08:00' });
  assert.equal(watchedAgent(lanes([stale])), null);
  const edge = seg({ to: '2026-11-08T10:00:05+08:00' });
  assert.ok(watchedAgent(lanes([edge])));
});

test('段指向的运行不在 agents 里，或已经结束：用默认包', () => {
  assert.equal(pickPackKind({ lanes: lanes([seg({ runId: 'run_x' })]), packsByTool: MAP, configPet: 'claude' }), 'claude');
  assert.equal(pickPackKind({ lanes: lanes([seg()], [agent({ endAt: '2026-11-08T10:00:10+08:00' })]), packsByTool: MAP, configPet: 'claude' }), 'claude');
});

test('缺 now 或 to 不合法：不认作在看（失败即默认）', () => {
  assert.equal(watchedAgent(lanes([seg()], [agent()], null)), null);
  assert.equal(watchedAgent(lanes([seg({ to: 'bad' })])), null);
});
