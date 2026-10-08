"""后台工人：替用户认规则认不出的窗口（agent.chat.v1 第十节；nexus-core v2.15「让 AI 认窗口」）。

nexus-core 够不着本服务（gateway.v1 第八节：AI 桥内网上没有它），所以由本服务定时去问：对每个认识的租户，
经 MCP 调一次 `get_window_awaiting_target`（普通 HTTP，不花模型的钱）。有窗口在等，就在该租户的「自动识别窗口」
会话里跑一轮——走与人发消息完全相同的那条路（main.py 的 `turn`：同样的限流、时限、存档、调试记录），提示词固定，
窗口本身由模型自己经 MCP 读（标题是别的电脑上来的文本，只当工具结果里的数据，不进提示词）。
任何失败只记日志：nexus-core 等不到回答，自己会转去请人选。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque

import httpx

from . import config

log = logging.getLogger("agent")
POLL_SECONDS = 25.0     # 多久问一次（nexus-core 认「这条路活着」的宽限是 60 秒）
TURN_SECONDS = 90.0     # 一轮的总时限：比 nexus-core 等回答的 120 秒短，到点中止
MAX_PER_HOUR = 12       # 每租户每小时至多跑这么多轮（nexus-core 那边是同一个数；这里再拦一道，省得白跑）
MAX_TENANTS = 64        # ponytail: 至多替这么多租户问；真有更多账号时改成分批轮着问
SESSION_TITLE = "自动识别窗口"
PROMPT = ("（后台自动任务，不是用户发的）有一个窗口分类规则认不出。请按系统提示里「认窗口」那一样办："
          "先调 get_window_awaiting_target 读它，读历史和项目后只调用一次 suggest_window_target（认不出就 none: true），"
          "最后用一句话说认到了哪。")


class Worker:
    def __init__(self, s: config.Settings, store, gens: dict, turn, mgr, http: httpx.AsyncClient | None = None,
                 clock=time.monotonic):
        self.s, self.store, self.gens, self.turn, self.mgr, self.clock = s, store, gens, turn, mgr, clock
        self.http = http or httpx.AsyncClient(timeout=10, trust_env=False)
        self.busy: dict[str, asyncio.Task] = {}      # 租户 → 正在问 / 正在跑的那一个任务
        self.turns: dict[str, deque] = {}            # 租户 → 最近一小时起过的轮次的时刻
        self._loop: asyncio.Task | None = None

    def start(self) -> None:
        self._loop = asyncio.create_task(self._run())

    async def stop(self) -> None:
        for t in [self._loop, *self.busy.values()]:
            if t:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        await self.http.aclose()

    async def _run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(POLL_SECONDS)

    def tenants(self) -> list[str]:
        """认识的租户：发过请求的（store 记着，重启不丢）；非严格模式再加单人租户。"""
        known = self.store.tenants()
        if not self.s.strict and config.LOCAL_TENANT not in known:
            known.insert(0, config.LOCAL_TENANT)
        return known[:MAX_TENANTS]

    def tick(self) -> None:
        """每个租户各起一个任务；上一个还没完的跳过——同一租户同时至多一轮，慢的租户不拖住别人。"""
        for tenant in self.tenants():
            if tenant not in self.busy:
                self.busy[tenant] = asyncio.create_task(self._guarded(tenant))

    async def _guarded(self, tenant: str) -> None:
        try:
            await self.once(tenant)
        except Exception as e:      # 连不上 MCP、运行时起不来、撞限流、超时……：只记类别，下一次再问
            log.warning("autotrack error category=internal sub=%s", type(e).__name__)
        finally:
            self.busy.pop(tenant, None)

    async def once(self, tenant: str) -> bool:
        """问一次；有窗口在等就跑一轮。返回跑没跑。"""
        if any(t == tenant for t, _ in self.gens):       # 人正在和助手聊：共用同一个限额与运行时，让人先
            return False
        now, turns = self.clock(), self.turns.setdefault(tenant, deque())
        while turns and now - turns[0] >= 3600:
            turns.popleft()
        if len(turns) >= MAX_PER_HOUR or not await self._awaiting(tenant):
            return False
        turns.append(now)
        stream, abandon = await self.turn(tenant, self._session(tenant), PROMPT)
        try:
            async with asyncio.timeout(TURN_SECONDS):
                async for _ in stream:
                    pass
        finally:
            abandon()       # 正常结束时什么都不做；超时 / 被取消 = 中止这一轮
        return True

    async def _awaiting(self, tenant: str) -> bool:
        """经 MCP 问 nexus-core：这个租户此刻有窗口在等 AI 认吗（这一问同时就是认领）。租户头与 opencode 那边同一条规则。"""
        headers = {} if tenant == config.LOCAL_TENANT else {"X-Nexus-Tenant": tenant}
        r = await self.http.post(self.s.mcp_url, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_window_awaiting_target", "arguments": {}}})
        r.raise_for_status()
        res = r.json().get("result") or {}
        return not res.get("isError") and bool((res.get("structuredContent") or {}).get("window"))

    def _session(self, tenant: str) -> str:
        """该租户专用的那个会话（没有就建；人在「AI助理」的会话列表里看得到每一轮）。每轮换一个新的模型上下文：
        这个窗口与上一个无关，也不让上下文越攒越长。"""
        sess = next((x for x in self.store.list(tenant) if x.get("title") == SESSION_TITLE), None)
        sess = sess or self.store.create(tenant, SESSION_TITLE)
        if sess.get("ocId"):
            # ponytail: 旧上下文只记下来，等下次拉起运行时再删（运行时闲 15 分钟就会收）；一直不闲、攒多了再改成当场删
            self.mgr.defer_delete(tenant, sess["ocId"])
            sess["ocId"] = None
            self.store.save(tenant, sess)
        return sess["id"]
