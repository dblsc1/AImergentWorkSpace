# worklog · 给 consulter 的纸条

- 日期：2026-07-29
- 角色：CFO
- tier：`simple`

## 为什么写

用户裁决：**consulter 改的框架，以后由用户审，我只辅助举证、不下 verdict。**
此前写法是「CFO 交叉审」，方向反了过来。

裁决一变，有三件事必须让 consulter 知道，而它们只活在我的会话里就等于没发生：

1. **口径变更本身**，以及 `agents/protocol/supervision.md` 第 21 行还写着旧口径。
   那份文件自称「谁审谁的唯一事实源」，**两处并存时读它的 agent 会照旧的做**。
   `agents/protocol/` 是框架区、归 consulter，我没代改，把三处待改落点写进纸条。
2. **上批框架有一个形状没扫完**（详见同目录的举证 worklog）。
3. **根级单文件进不了仓**（本轮我自己撞上的新缺陷）。

## 落点选择

项目级角色（CFO / consulter）目前**没有沟通留痕的约定落点**——
约定只覆盖到模块内实例（`codeagent/<容器>/<编号>/docs/comm.jsonl`）与模块 arbiter。

所以我落在自己写区：纸条正文 `agents/cfo/docs/notes/`，
并用 `scripts/log_event.sh` 往 `agents/cfo/docs/comm.jsonl` 追加一条索引。
**没有写进 `agents/consulter/` 下** —— 那是它的写区，往别人写区塞东西是未受控双写。

这个落点缺失本身值得补进约定，但那是框架的事，我只在这里记一笔。

## 依赖漂移说明

无。本次只写留痕与一条 append-only 沟通记录，未触碰代码、契约、依赖。
