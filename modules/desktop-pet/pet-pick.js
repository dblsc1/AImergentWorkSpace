// 桌宠 · pet-pick.js —— 按代理工具挑宠物包（desktop.pet.v1「三·1」「宠物包与对照」）。纯函数，无 IO。
const { DEFAULT_PACKS_BY_TOOL } = require('./pack');

const norm = s => String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();

// 人此刻在看的那条代理运行：焦点在电脑前，窗口标题与运行的 label 相同（≥ 3 字符）。
function watchedAgent(current) {
  const f = current && current.focus;
  if (!f || f.state !== 'present') return null;
  const title = norm(f.title);
  if (title.length < 3) return null;
  return (current.agents || []).find(a => norm(a.label) === title) || null;
}

// 返回该用的宠物包种类（'' = 内置形象）。包是否已下载由调用方判断。
function pickPackKind({ current, packsByTool, configPet }) {
  const agent = watchedAgent(current);
  if (!agent) return configPet || '';
  const map = packsByTool && typeof packsByTool === 'object' ? packsByTool : DEFAULT_PACKS_BY_TOOL;
  const pick = key => (Object.hasOwn(map, key) && typeof map[key] === 'string' && map[key]) || null;
  return pick(agent.tool) || pick('*') || DEFAULT_PACKS_BY_TOOL['*'];
}

module.exports = { pickPackKind, watchedAgent };
