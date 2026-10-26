"""``GET /api/core/views/history``（契约 v2.39「MCP v0.5 的后端」二，唯一事实源）：人和 agent 分开给，永不相加。**只读**。

数据来源（都是既有读路径）：人已入账的 = ``proj_daily_stats``（按天 / 目标）与 ``proj_lanes``（段）；还没分类的 = tiers 开时的窗口块与原始记录
（``activity.blocks``），tiers 关时的待确认建议（口径同 ``suggestions/pending-days``）；代理 = ``collab_runs``。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from bisect import bisect_right
from datetime import date as Date, datetime, timedelta

from ...config import settings
from ...tenant import current as current_tenant
from ..activity import activity_settings, block_rules, blocks, blocks_repo, collab_repo, pending_days
from ..activity import gaps as gaps_mod
from ..planner import service as planner_service
from ..planner.errors import UnprocessableError
from ..projector.handlers import daily_stats as daily_stats_projection
from ..projector.handlers import lanes as lanes_projection
from . import address as address_mod
from . import collab_runs as cr
from .queries import _today

WHO = ("human", "agent", "both")
DETAIL = ("summary", "segments", "windows")
MAX_DAYS = {"summary": 92, "segments": 7, "windows": 7}
CAP_SEGMENTS, CAP_BLOCKS, RUNS_PAGE, CAP_HANDOFFS, CAP_BY_TARGET, CAP_RECORDS = 2000, 500, 200, 2000, 200, 50
CAP_PENDING_BLOCKS = 20000  # 待分类块（日 × 块名）至多这么多，超了 truncated
GAP = 300  # 「三级分类」四：相邻记录间隔 ≤ 300 秒连成一段
TEXT_MAX = 80
REPLY_WINDOW = 60  # 一段 working 的开头这么多秒内有人回复 → startedBy
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_READ_LIMIT = 10000  # 已入账段一次最多读这么多（再按 2000 封顶）；ponytail: 7 天内一般远小于，不够再分页


def one_line(text: str | None, limit: int = TEXT_MAX) -> str:
    """屏幕来的文字：去控制 / 不可见字符、空白折成一格、≤ limit 个字符。"""
    t = "".join(c for c in (text or "") if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn") or c.isspace())
    return " ".join(t.split())[:limit]


def _day(raw: str | None, name: str, default: Date) -> Date:
    if raw is None:
        return default
    if not _DATE.match(raw):
        raise UnprocessableError(f"参数 {name} 不是合法日期（YYYY-MM-DD）：{raw!r}")
    try:
        return Date.fromisoformat(raw)
    except ValueError as exc:
        raise UnprocessableError(f"参数 {name} 不是合法日期：{raw!r}") from exc


def _enum(raw: str, name: str, allowed: tuple) -> str:
    if raw not in allowed:
        raise UnprocessableError(f"参数 {name} 取值须为 {' | '.join(allowed)}：{raw!r}")
    return raw


def _tz_offset(now: datetime) -> str:
    return now.astimezone(settings.tz).isoformat()[-6:]


def _days(first: Date, last: Date) -> list[Date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


# ------------------------------------------------------------------ 人


def _one_window(app: str | None, title: str | None) -> str:
    return one_line(f"{app} · {title}" if title else app)


def _cluster(recs: list[dict]) -> list[dict]:
    """一个块的记录 → 连续段（相邻间隔 ≤ 300 秒；段秒数 = 段内时间并集）。"""
    out: list[dict] = []
    for r in sorted(recs, key=lambda r: r["startTs"]):
        if out and (r["startTs"] - out[-1]["to"]).total_seconds() <= GAP:
            cur = out[-1]
            cur["spans"].append((r["startTs"], r["endTs"]))
            cur["to"] = max(cur["to"], r["endTs"])
        else:
            out.append({"from": r["startTs"], "to": r["endTs"], "spans": [(r["startTs"], r["endTs"])]})
    for seg in out:
        seg["seconds"] = round(cr.seconds(cr.union(seg["spans"])))
    return out


class _Human:
    """人的部分。planner（编号 / 分区 / 项目）与 tiers 开关都惰性读、各只读一次（``who=agent`` 一次都不读）。"""

    def __init__(self, user: str, first: Date, last: Date, scope: str | None, detail: str):
        self.user, self.first, self.last, self.scope, self.detail = user, first, last, scope, detail
        self.start, self.end = cr.midnight(first), cr.midnight(last + timedelta(days=1))
        self.truncated = False
        self._plan: tuple | None = None
        self._tiers: bool | None = None
        self._rows: list[dict] | None = None
        self.uncaught = 0  # v0.4.1：还没分类的秒数里「第一级没收」的部分（其余是第一级收了、第二级没定）

    @property
    def planner(self) -> tuple[address_mod.Addresses, dict, dict, set]:
        if self._plan is None:
            zones, projects, tasks = planner_service.list_zones(), planner_service.list_projects(), planner_service.list_tasks()
            self._plan = (address_mod.build(zones, projects, tasks, planner_service.creation_order()),
                          {p["id"]: p.get("zoneId") for p in projects}, {t["id"]: t.get("projectId") for t in tasks},
                          {x["id"] for x in (*zones, *projects, *tasks)})
        return self._plan

    @property
    def addrs(self) -> address_mod.Addresses:
        return self.planner[0]

    @property
    def tiers(self) -> bool:
        if self._tiers is None:
            self._tiers = activity_settings.tiers_on()
        return self._tiers

    def check_scope(self) -> None:
        if self.scope is not None and self.scope not in self.planner[3]:
            raise UnprocessableError(f"参数 scope 不是已有的分区 / 项目 / 任务 id：{self.scope!r}")

    def in_scope(self, task_id: str | None, project_id: str | None) -> bool:
        _, zone_of, proj_of, _ = self.planner
        project_id = project_id or proj_of.get(task_id)  # 窗口块的 part 只给任务时，项目 / 分区从任务查
        return self.scope is None or self.scope in (task_id, project_id, zone_of.get(project_id))

    # -- 已入账
    def confirmed(self) -> tuple[int, dict[str, int], dict[str, list]]:
        """汇总与按目标：直接读投影 ``proj_daily_stats``，所以跨午夜的一段整段归开始那天（与投影同一口径）。"""
        rows = [r for r in daily_stats_projection.read_daily_stats(self.user, self.first.isoformat(), self.last.isoformat())
                if self.in_scope(r.get("taskId"), r["projectId"])]
        per_day: dict[str, int] = {}
        targets: dict[str, dict[tuple, int]] = {}
        for r in rows:
            per_day[r["date"]] = per_day.get(r["date"], 0) + r["seconds"]
            key = (r.get("taskId"), r["projectId"])
            targets.setdefault(r["date"], {})[key] = targets.get(r["date"], {}).get(key, 0) + r["seconds"]
        by_target: dict[str, list] = {}
        for d, m in targets.items():
            rows_d = sorted(m.items(), key=lambda kv: (-kv[1], kv[0][0] or "", kv[0][1]))
            if len(rows_d) > CAP_BY_TARGET:
                self.truncated = True
            by_target[d] = [{"taskId": t, "projectId": p, "address": self.addrs.target(t, p), "seconds": s}
                            for (t, p), s in rows_d[:CAP_BY_TARGET]]
        return sum(per_day.values()), per_day, by_target

    def confirmed_segments(self, attention) -> list[dict]:
        """段按实际起止给：与范围有重叠的都列（前一天开始、跨零点进来的也在，``from`` 是真实起点）；
        与 ``byDay`` 不同——byDay 按投影整段归开始那天。按范围升序读，超过读取上限时 ``truncated``（丢的是最晚的，不是最老的）。"""
        rows = lanes_projection.read_lanes(self.user, "session", self.start, self.end, _READ_LIMIT + 1, ascending=True)
        self.truncated |= len(rows) > _READ_LIMIT
        out = []
        for r in rows[:_READ_LIMIT]:
            if not self.in_scope(r.get("taskId"), r.get("projectId")):
                continue
            out.append({"from": cr.iso(r["startAt"]), "to": cr.iso(r["endAt"]), "seconds": r["durationSeconds"],
                        "status": "confirmed", "eventId": r.get("eventId"), "taskId": r.get("taskId"),
                        "projectId": r.get("projectId"), "address": self.addrs.target(r.get("taskId"), r.get("projectId")),
                        "window": one_line(r["block"]) if r.get("block") else None,
                        "onAgentRun": attention(r["startAt"], r["endAt"]), "_t": r["startAt"]})
        return out

    # -- 还没分类
    def pending(self) -> tuple[dict[str, int], list[tuple[datetime, datetime, str, int]]]:
        """→ (每天秒数, 段 [(from, to, 块名 / 窗口, 秒数)])。"""
        if self.tiers:
            return self._pending_tiers()
        days = pending_days.pending_days(self.first, self.last)["days"]
        per_day = {d["date"]: int(d["seconds"]) for d in days}
        self.uncaught = sum(per_day.values())  # tiers 关：没有第二级，都算第一级没收
        segs = []
        if self.detail == "segments":
            rows = collab_repo.pending_suggestions(self.user, self.start, self.end, CAP_SEGMENTS + 1)
            self.truncated |= len(rows) > CAP_SEGMENTS
            segs = [(r["startTs"], r["startTs"] + timedelta(seconds=r["durationSeconds"]), _one_window(r.get("app"), r.get("title")),
                     r["durationSeconds"]) for r in rows[:CAP_SEGMENTS]]
        return per_day, segs

    def all_records(self) -> list[dict]:
        """detail=segments / windows：范围内的原始记录只读这一次（待分类段与窗口块共用）。"""
        if self._rows is None:
            self._rows = collab_repo.range_records(self.user, self.first.isoformat(), self.last.isoformat())
        return self._rows

    def _classify(self, pairs) -> dict[tuple[str, str], tuple]:
        """(日, 块名) → ``(分类文档, 通用窗口标记日, 第二级规则, 是否在 14 天内, 生效状态 resolve 的结果)``；整个范围各读一次，不逐天。"""
        docs = collab_repo.block_docs(self.user, self.first.isoformat(), self.last.isoformat())
        generic = blocks_repo.generic_since(self.user)
        cutoff = blocks.today() - timedelta(days=blocks.RULE_DAYS)
        need = sorted({k for ds, k in pairs if Date.fromisoformat(ds) >= cutoff
                       and ((docs.get((ds, k)) or {}).get("status") is None or docs[(ds, k)].get("via") == "rule")
                       and not (k in generic and generic[k] <= ds)})
        rules = block_rules.effective(self.user, need) if need else {}
        out = {}
        for ds, k in pairs:
            d = Date.fromisoformat(ds)
            recent = d >= cutoff
            doc, since, rule = docs.get((ds, k)), generic.get(k), rules.get(k) if recent else None
            out[(ds, k)] = (doc, since, rule, recent, blocks.resolve(d, doc, since, rule, recent))
        return out

    def _pending_tiers(self):
        """tiers 开：pending / 还没拆完的通用窗口块里、尚未入账的记录。summary 只要每天的秒数（一次 Mongo 聚合）；
        segments 复用已读的记录。待分类块至多 ``CAP_PENDING_BLOCKS`` 个，超了 ``truncated``。"""
        if self.detail == "segments":
            by_pair: dict[tuple[str, str], list] = {}
            for r in self.all_records():
                if r.get("ledgerEventId") is None:
                    by_pair.setdefault((r["date"], r["blockKey"]), []).append(r)
            spans = {p: [(r["startTs"], r["endTs"]) for r in rs] for p, rs in by_pair.items()}
            ruled = {p for p, rs in by_pair.items() if any(r.get("keySource") == "rule" for r in rs)}
        else:
            rows = collab_repo.pending_spans(self.user, self.first.isoformat(), self.last.isoformat(), CAP_PENDING_BLOCKS + 1)
            spans = {(r["date"], r["blockKey"]): r["spans"] for r in rows}
            ruled = {(r["date"], r["blockKey"]) for r in rows if r.get("rule")}
            by_pair = {}
        states = self._classify(spans)
        open_pairs = sorted(p for p, st in states.items() if st[4][0] == "pending" or (st[4][0] == "generic" and not st[4][3]))
        if len(open_pairs) > CAP_PENDING_BLOCKS:
            self.truncated = True
            open_pairs = open_pairs[:CAP_PENDING_BLOCKS]
        per_day: dict[str, list] = {}
        for ds, k in open_pairs:
            per_day.setdefault(ds, []).extend(spans[(ds, k)])
        out_day = {d.isoformat(): round(cr.seconds(cr.union(per_day.get(d.isoformat(), [])))) for d in _days(self.first, self.last)}
        auto_day: dict[str, list] = {}
        for ds, k in open_pairs:
            if (ds, k) not in ruled:
                auto_day.setdefault(ds, []).extend(spans[(ds, k)])
        self.uncaught = sum(round(cr.seconds(cr.union(v))) for v in auto_day.values())
        segs = [(s["from"], s["to"], one_line(k), s["seconds"]) for ds, k in open_pairs for s in _cluster(by_pair.get((ds, k), []))]
        return out_day, segs

    # -- 窗口块
    def windows(self) -> list[dict]:
        if not self.tiers:
            return []
        rows = self.all_records()
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(r["date"], []).append(r)
        states = self._classify({(r["date"], r["blockKey"]) for r in rows})
        out: list[dict] = []
        for d in _days(self.first, self.last):
            ds = d.isoformat()
            recs = by_day.get(ds, [])
            groups = blocks._group(recs)  # noqa: SLF001
            items = sorted((blocks._item(d, k, g, *states[(ds, k)][:4]) for k, g in groups.items()),  # noqa: SLF001
                           key=lambda i: (-i["seconds"], i["blockKey"]))
            for it in items:
                tgt = [self.addrs.target(p.get("taskId"), p.get("projectId")) for p in it["parts"]]
                if self.scope is not None and not any(self.in_scope(p.get("taskId"), p.get("projectId")) for p in it["parts"]):
                    continue
                mine = sorted((r for r in recs if r["blockKey"] == it["blockKey"]), key=lambda r: (-r["seconds"], r["startTs"]))
                top = sorted(mine[:CAP_RECORDS], key=lambda r: r["startTs"])
                out.append({**it, "parts": [{**p, "address": a} for p, a in zip(it["parts"], tgt)], "date": ds,
                            "address": tgt[0] if len(tgt) == 1 else None,
                            "records": [{"from": cr.iso(r["startTs"]), "to": cr.iso(r["endTs"]), "seconds": r["seconds"],
                                         "app": r["app"], "title": one_line(r["title"])} for r in top],
                            **({"recordsTruncated": True} if len(mine) > CAP_RECORDS else {})})
                if len(out) > CAP_BLOCKS:  # 到上限 + 1 就停（够判截断了）
                    self.truncated = True
                    return out[:CAP_BLOCKS]
        return out


def _on_run(runs: list[dict]):
    """人的窗口就是某条运行的终端（v2.17 attend）：与这一段有交集的运行恰好一条 → 它的 runId。"""
    def attention(a: datetime, b: datetime) -> str | None:
        hit = {r["runId"] for r in runs if any(min(y, b) > max(x, a) for x, y in r["attend"])}
        return hit.pop() if len(hit) == 1 else None
    return attention


# ------------------------------------------------------------------ agent


def _clip_working(runs: list[dict], start: datetime, end: datetime) -> dict[str, list]:
    """runId → 钳到范围内的干活区间（只算一次，下面几处显式传参共用）。"""
    return {r["runId"]: [c for a, b in cr.working(r) if (c := cr.clip(a, b, start, end))] for r in runs}


def _cursor_of(run: dict) -> str:
    raw = json.dumps([run["startAt"].isoformat(), run["runId"]], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _cursor_key(cursor: str) -> tuple[datetime, str]:
    try:
        at, run_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        moment = datetime.fromisoformat(at)
        if moment.tzinfo is None:
            raise ValueError("naive")
        return moment, str(run_id)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise UnprocessableError(f"参数 runsCursor 不是上一页给的游标：{cursor[:40]!r}") from exc


def _agents(runs: list[dict], work: dict[str, list], start: datetime, end: datetime, detail: str, over: bool,
            cursor: str | None = None) -> tuple[dict, bool]:
    per_day: dict[str, float] = {}
    by_agent: dict[tuple, list] = {}
    for run in runs:
        clipped = work[run["runId"]]
        for d, s in cr.split_days(clipped).items():
            per_day[d] = per_day.get(d, 0.0) + s
        row = by_agent.setdefault((run["agent"], run["label"]), [0.0, 0])
        row[0] += cr.seconds(clipped)
        row[1] += 1
    days = [d.isoformat() for d in _days(start.astimezone(settings.tz).date(), (end - timedelta(seconds=1)).astimezone(settings.tz).date())]
    out = {
        "workingSeconds": round(sum(per_day.values())),
        "byDay": [{"date": d, "workingSeconds": round(per_day.get(d, 0.0))} for d in days],
        "byAgent": [{"agent": a, "label": lb, "workingSeconds": round(s), "runs": n}
                    for (a, lb), (s, n) in sorted(by_agent.items(), key=lambda kv: (-kv[1][0], str(kv[0])))],
    }
    truncated = over
    if detail == "segments":
        # v2.43：startAt 升序翻页（同刻按 runId），每页 RUNS_PAGE 条；汇总上面已按全部运行算。
        ordered = sorted(runs, key=lambda r: (r["startAt"], r["runId"]))
        if cursor:
            after = _cursor_key(cursor)
            ordered = [r for r in ordered if (r["startAt"], r["runId"]) > after]
        page = ordered[:RUNS_PAGE]
        out["runs"] = [_run_view(r, work[r["runId"]], start, end) for r in page]
        out["nextRunsCursor"] = _cursor_of(page[-1]) if len(ordered) > RUNS_PAGE else None
    return out, truncated


def _snaps(t: datetime, a: datetime, b: datetime) -> bool:
    """回复 t 算 working 段 [a, b) 开的头：落在开头 REPLY_WINDOW 秒内且段还没结束；已结束的段不回头认。"""
    return a <= t <= a + timedelta(seconds=REPLY_WINDOW) and t < b


def _started_by(run: dict, a: datetime, b: datetime) -> bool:
    return any(_snaps(t, a, b) for t in run["replies"])


def _turns(run: dict, start: datetime, end: datetime) -> list[dict]:
    """按指令切段（v2.43）：一段从一次人回复开始，到下一次回复之前最后一个 working 相位结束；
    第一次回复之前的 working 归一段 startedBy=None。回复落在 working 开头 REPLY_WINDOW 秒内且该段还没结束，才算它开的头（同 startedBy）；落在等待里的回复开新段，不回头并入已结束的段。
    working 跨过回复时在回复处切开。只含与 [start, end) 相交的部分，没有 working 的回复不成段。"""
    works = cr.working(run)
    cuts = sorted({next((a for a, b in works if _snaps(t, a, b)), t) for t in run["replies"]})
    turns: dict[int, list] = {}
    for a, b in works:
        pts = [a, *(c for c in cuts if a < c < b), b]
        for x, y in zip(pts, pts[1:]):
            if c := cr.clip(x, y, start, end):
                t = turns.setdefault(bisect_right(cuts, x), [c[0], c[1], 0.0])
                t[0], t[1], t[2] = min(t[0], c[0]), max(t[1], c[1]), t[2] + (c[1] - c[0]).total_seconds()
    return [{"from": cr.iso(f), "to": cr.iso(t), "workingSeconds": round(s), "startedBy": "human_reply" if i else None}
            for i, (f, t, s) in sorted(turns.items())]


def _run_view(run: dict, clipped: list, start: datetime, end: datetime) -> dict:
    phases = []
    for a, b, ph in run["segs"]:
        c = cr.clip(a, b, start, end)
        if c:
            phases.append({"from": cr.iso(c[0]), "to": cr.iso(c[1]), "phase": ph,
                           **({"startedBy": "human_reply"} if ph == "working" and _started_by(run, a, b) else {})})
            # 契约待定：subagents——运行里没有子代理数据，不给该键
    attn = [c for a, b in run["attend"] if (c := cr.clip(a, b, start, end))]
    return {"runId": run["runId"], "agent": run["agent"], "label": run["label"], "startAt": cr.iso(run["startAt"]),
            "endAt": cr.iso(run["endAt"]) if run["endAt"] else None, "phases": phases, "turns": _turns(run, start, end),
            "workingSeconds": round(cr.seconds(clipped)), "humanAttentionSeconds": round(cr.seconds(attn))}


def _handoffs(runs: list[dict], start: datetime, end: datetime) -> tuple[list[dict], bool]:
    out = []
    for run in runs:
        for a, b, ph in run["segs"]:
            if not start <= a < end:
                continue
            if ph == "working" and _started_by(run, a, b):
                out.append({"at": cr.iso(a), "runId": run["runId"], "type": "human_to_agent", "_t": a})
            elif ph in ("waiting_input", "waiting_permission"):
                # 到下一次人回复、或人开始看它（attend 的下一段开头；已经在看 = 马上）为止，先到者；范围结束前没有 = null
                cands = [t for t in run["replies"] if t > a] + [max(x, a) for x, y in run["attend"] if y > a]
                nxt = min(cands, default=None)
                out.append({"at": cr.iso(a), "runId": run["runId"], "type": "agent_to_human", "_t": a,
                            "reason": "permission" if ph == "waiting_permission" else "input",
                            "waitedSeconds": round((nxt - a).total_seconds()) if nxt is not None and nxt < end else None})
    out.sort(key=lambda h: h["_t"])
    cut = len(out) > CAP_HANDOFFS
    return [{k: v for k, v in h.items() if k != "_t"} for h in out[:CAP_HANDOFFS]], cut


def _overlap(runs: list[dict], work: dict[str, list]) -> dict:
    marks = sorted([(a, 1) for r in runs for a, _ in work[r["runId"]]] + [(b, -1) for r in runs for _, b in work[r["runId"]]],
                   key=lambda m: (m[0], m[1]))  # 同刻先收后放
    cur = peak = 0
    for _, delta in marks:
        cur += delta
        peak = max(peak, cur)
    busy = cr.union([c for r in runs for c in work[r["runId"]]])
    watched = cr.union([c for r in runs for a, b in r["attend"] for w in work[r["runId"]] if (c := cr.clip(a, b, *w))])
    return {"maxAgentsWorkingAtOnce": peak,
            "secondsWithAgentsWorkingWhileHumanElsewhere": round(cr.seconds(busy) - cr.seconds(watched))}


# ------------------------------------------------------------------ 入口


def get_history(date_from: str | None, date_to: str | None, who: str, detail: str, scope: str | None,
                runs_cursor: str | None = None) -> dict:
    _enum(who, "who", WHO)
    _enum(detail, "detail", DETAIL)
    if runs_cursor:
        _cursor_key(runs_cursor)  # 先验：游标不对即使 who=human 也 422
    today = Date.fromisoformat(_today())
    first, last = _day(date_from, "from", today), _day(date_to, "to", today)
    if first > last:
        raise UnprocessableError(f"参数 from 不能晚于 to：{first}…{last}")
    if (last - first).days + 1 > MAX_DAYS[detail]:
        raise UnprocessableError(f"参数 detail={detail} 的跨度（from…to）最多 {MAX_DAYS[detail]} 天：{first}…{last}")

    user = current_tenant()
    h = _Human(user, first, last, scope or None, detail)
    h.check_scope()
    want_h, want_a = who in ("human", "both"), who in ("agent", "both")
    segs_mode = detail == "segments"
    runs: list[dict] = []
    now, over = datetime.now(settings.tz), False
    if want_a or segs_mode:  # 人的段要认「在看哪条运行」，所以 who=human 的 segments 也读运行
        now, runs, over = cr.load(h.start, h.end)

    out: dict = {"range": {"from": first.isoformat(), "to": last.isoformat(), "tz": _tz_offset(now), "today": today.isoformat()}}
    if want_h:
        total, per_day, by_target = h.confirmed()
        pend_day, pend_segs = ({}, []) if h.scope else h.pending()
        human: dict = {
            "confirmedSeconds": total,
            "pendingSeconds": None if h.scope else sum(pend_day.values()),
            **({} if h.scope else {"pendingUncaughtSeconds": min(h.uncaught, sum(pend_day.values())),
                                   "pendingUnassignedSeconds": sum(pend_day.values()) - min(h.uncaught, sum(pend_day.values()))}),
            "byDay": [{"date": d.isoformat(), "seconds": per_day.get(d.isoformat(), 0),
                       "pendingSeconds": None if h.scope else pend_day.get(d.isoformat(), 0),
                       "byTarget": by_target.get(d.isoformat(), [])} for d in _days(first, last)],
        }
        if segs_mode:
            attention = _on_run(runs)
            segs = h.confirmed_segments(attention) + [
                {"from": cr.iso(a), "to": cr.iso(b), "seconds": s, "status": "pending", "eventId": None, "taskId": None,
                 "projectId": None, "address": None, "window": w, "onAgentRun": attention(a, b), "_t": a}
                for a, b, w, s in pend_segs]
            segs.sort(key=lambda s: s["_t"])
            if len(segs) > CAP_SEGMENTS:
                h.truncated = True
                segs = segs[:CAP_SEGMENTS]  # 契约待定：超上限时留最早的（按范围升序读，丢的是最晚的）
            human["segments"] = [{k: v for k, v in s.items() if k != "_t"} for s in segs]
            if not h.scope:  # v2.40 原始记录的空洞；不属于任何任务，按 scope 过滤时不给
                human["gaps"] = gaps_mod.range_gaps(list(_days(first, last)))
        if detail == "windows":
            human["blocks"] = h.windows()
            if not h.tiers:
                out["tiersOff"] = True
        out["human"] = human
    truncated = h.truncated
    if want_a:
        work = _clip_working(runs, h.start, h.end)
        out["agents"], cut = _agents(runs, work, h.start, h.end, detail, over, runs_cursor)
        truncated |= cut
        if segs_mode and who == "both":
            out["handoffs"], cut = _handoffs(runs, h.start, h.end)
            out["overlap"] = _overlap(runs, work)
            truncated |= cut
    out["truncated"] = truncated
    return out
