"""三级分类两个测试文件共用的接口桩（page.route，一个字节都不写库）。"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from conftest import AUTO_PAGE, TREE, open_page
from playwright.sync_api import Route

# 两个项目分属两个分区，颜色不同（吉他练习 = 练琴区 #35c9eb，考级计划 = 制作区 #ff9d45）
TREE2 = copy.deepcopy(TREE)
TREE2["projects"][1]["zoneId"] = "z_work"
TREE2["projects"][1]["tasks"] = [{"id": "t_exam", "key": "x", "name": "模拟考", "done": False}]
CYAN, ORANGE = "#35c9eb", "#ff9d45"

OLD = {"id": "r_old", "app": "code", "title": None, "taskId": "t_word", "confidence": 0.9, "note": "编辑器",
       "enabled": True, "migrated": True}
OLD_AI = {"id": "r_ai", "app": None, "title": "视奏", "taskId": None, "mode": "ai", "confidence": 0.9, "note": None,
          "enabled": True}
B1 = {"id": "r_b1", "kind": "block", "app": "code", "title": "garden", "block": "花园代码", "enabled": True,
      "hitCount": 312}
B2 = {"id": "r_b2", "kind": "block", "app": "ptyxis", "title": None, "block": "终端", "enabled": True}
B3 = {"id": "r_b3", "kind": "block", "app": "wps", "title": None, "block": "未分配", "enabled": True}
RULES = [OLD, B1, OLD_AI, B2, B3]


def j(route: Route, code: int, obj: Any) -> None:
    route.fulfill(status=code, content_type="application/json", body=json.dumps(obj, ensure_ascii=False))


class AcctStub:
    """activity/settings。"""

    def __init__(self, tiers: bool = False, status: int = 200) -> None:
        self.doc = {"version": 3, "tiers": tiers, "tiersSince": None,
                    "agentDirect": {"enabled": False, "windowsClassify": False, "honeycombRecords": False}}
        self.status = status
        self.calls: list[tuple[str, str | None, Any]] = []
        self.put_reply: tuple[int, dict] | None = None

    def route(self, route: Route) -> None:
        req = route.request
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, req.headers.get("if-match"), body))
        if self.status != 200:
            return j(route, self.status, {"detail": "Not Found"})
        if req.method == "PUT":
            if self.put_reply:
                return j(route, *self.put_reply)
            if req.headers.get("if-match") != f'"{self.doc["version"]}"':
                return j(route, 412, {"detail": "stale", "currentVersion": self.doc["version"]})
            self.doc = {**body, "version": self.doc["version"] + 1, "tiersSince": "2026-10-24T00:00:00+00:00"}
        return j(route, 200, self.doc)

    def puts(self) -> list[tuple[str, str | None, Any]]:
        return [c for c in self.calls if c[0] == "PUT"]


class TierStub:
    """detector/rules + activity/block-rules + activity/proposals。"""

    def __init__(self, rules: list[dict] | None = None, block_rules: list[dict] | None = None,
                 proposals: dict | None = None) -> None:
        self.version = 7
        self.rules = copy.deepcopy(RULES if rules is None else rules)
        self.block_rules = copy.deepcopy(block_rules if block_rules is not None else [
            {"id": "br_1", "blockKey": "花园代码", "taskId": "t_word", "projectId": None, "author": "human"},
            {"id": "br_2", "blockKey": "终端", "taskId": None, "projectId": "p_book", "author": "human"},
            {"id": "br_3", "blockKey": "老块", "taskId": "t_gone", "projectId": None, "broken": True},
        ])
        self.proposals = proposals or {"projects": [], "tasks": [], "changes": [], "ignores": []}
        self.calls: list[tuple[str, str, str | None, Any]] = []
        self.put_reply: tuple[int, dict] | None = None

    def rules_route(self, route: Route) -> None:
        req = route.request
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, "rules", req.headers.get("if-match"), body))
        if req.method == "PUT":
            if self.put_reply:
                return j(route, *self.put_reply)
            if req.headers.get("if-match") != f'"{self.version}"':
                return j(route, 412, {"detail": "stale", "currentVersion": self.version})
            self.version += 1
            self.rules = [{**r, "id": r.get("id") or f"r_srv{i}"} for i, r in enumerate(body["rules"])]
        j(route, 200, {"version": self.version, "updatedAt": None, "rules": self.rules, "snapshotCount": 0,
                       "snapshotAt": None, "aiDirectRules": False})

    def block_route(self, route: Route) -> None:
        req = route.request
        body = json.loads(req.post_data) if req.post_data else None
        tail = req.url.split("/api/core/activity/block-rules", 1)[1]
        self.calls.append((req.method, "block-rules" + tail, None, body))
        if req.method == "POST":
            hit = [b for b in self.block_rules if b["blockKey"] == body["blockKey"]]
            new = {"id": hit[0]["id"] if hit else "br_new", "taskId": None, "projectId": None, "author": "human", **body}
            self.block_rules = [b for b in self.block_rules if b["blockKey"] != body["blockKey"]] + [new]
        elif req.method == "DELETE":
            self.block_rules = [b for b in self.block_rules if "/" + b["id"] != tail]
            return route.fulfill(status=204)
        j(route, 200, {"items": self.block_rules})

    def prop_route(self, route: Route) -> None:
        req = route.request
        body = json.loads(req.post_data) if req.post_data else None
        tail = req.url.split("/api/core/activity/proposals", 1)[1]
        if req.method == "GET":
            return j(route, 200, self.proposals)
        self.calls.append((req.method, "proposals" + tail, None, body))
        pid = tail.split("/")[1]
        for k, v in self.proposals.items():
            self.proposals[k] = [p for p in v if p.get("id") != pid]
        j(route, 200, {"ok": True})

    def routes(self) -> dict:
        return {r"/api/core/detector/rules": self.rules_route,
                r"/api/core/activity/block-rules": self.block_route,
                r"/api/core/activity/proposals": self.prop_route}

    def writes(self) -> list[tuple[str, str, str | None, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


def tree_route(route: Route) -> None:
    j(route, 200, TREE2)


def tier_page(browser, base, acct: AcctStub, tier: TierStub | None = None, extra: dict | None = None, **kw):
    routes = {r"/api/core/views/tree(\?includeEphemeral=true)?$": tree_route,
              r"/api/core/activity/settings": acct.route}
    if tier:
        routes.update(tier.routes())
    routes.update(extra or {})
    kw.setdefault("page_name", AUTO_PAGE)
    return open_page(browser, base, routes=routes, **kw)


def wait_tiers(page) -> None:
    page.wait_for_selector("#tier-rules:not([hidden]) #tier1-list li")
