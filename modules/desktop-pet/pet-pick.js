// 桌宠 · pet-pick.js —— 按代理工具挑宠物包（desktop.pet.v1「三·1」「宠物包与对照」）。纯函数，无 IO。
const { DEFAULT_PACKS_BY_TOOL } = require('./pack');

const PRESENT_WINDOW_MS = 15000;

// 人此刻在看的那条代理运行：views/lanes 的 human.presence 里最新一段（to 最晚）
// 不是离开、带 runId、结束时间离服务端此刻不超过 15 秒，且该 runId 还在跑（agents[] 里 endAt 为空）。
function watchedAgent(lanes) {
  if (!lanes || !lanes.human || !Array.isArray(lanes.human.presence)) return null;
  const now = Date.parse(lanes.now);
  let last = null, lastTo = -Infinity;
  for (const seg of lanes.human.presence) {
    const to = Date.parse(seg.to);
    if (Number.isFinite(to) && to > lastTo) { last = seg; lastTo = to; }
  }
  if (!last || last.afk || !last.runId || !(now - lastTo <= PRESENT_WINDOW_MS)) return null;
  return (lanes.agents || []).find(a => a.runId === last.runId && !a.endAt) || null;
}

// 返回该用的宠物包种类（'' = 内置形象）。包是否已下载由调用方判断。
function pickPackKind({ lanes, packsByTool, configPet }) {
  const agent = watchedAgent(lanes);
  if (!agent) return configPet || '';
  const map = packsByTool && typeof packsByTool === 'object' ? packsByTool : DEFAULT_PACKS_BY_TOOL;
  const pick = key => (Object.hasOwn(map, key) && typeof map[key] === 'string' && map[key]) || null;
  return pick(agent.tool) || pick('*') || DEFAULT_PACKS_BY_TOOL['*'];
}

module.exports = { pickPackKind, watchedAgent };
