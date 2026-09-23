#!/usr/bin/env python3
"""HoneyComb · 演示数据种子（全是编的，跟任何人的真实安排无关）。

用法（先把栈起起来）：

    docker compose up -d
    read -rsp '口令: ' HONEYCOMB_PASSWORD && export HONEYCOMB_PASSWORD
    python3 seed/seed_demo.py                       # 默认打 http://127.0.0.1:8800
    python3 seed/seed_demo.py --base http://其他地址  # 换入口（挂子路径就带上：http://host/Cockpit）
    HONEYCOMB_USER=alice python3 seed/seed_demo.py  # 账号登录（auth.gate v1.1），灌进这个账号的数据
    python3 seed/seed_demo.py --big                 # 大盘：10 分区 / 40 项目，看布局压力

做四件事：
  0. 先过登录门（v0.2 的入口是有门的，不登录什么都写不进去）；
  1. 建分区、项目、任务（默认 4 分区 9 项目；`--big` 是 10 分区 40 项目）；
  2. 用 /api/core/timer/backfill 给过去 14 天补一批"已经干过"的时间段，
     这样蜂巢的热度、分区规划面板的投入统计、计时档案一打开就有东西看；
  3. 幂等：同名的分区/项目/任务已存在就跳过；补登**每次都会重投**，
     但服务端按 dedupe 键去重，所以 events 总数不会翻倍。
     （2026-09-17 实测：连跑三遍，events total 恒为 43。）

口令只从环境变量 HONEYCOMB_PASSWORD 读，**不收命令行参数** —— 命令行参数会
出现在 ps 输出里。用 `read -rsp` 而不是 `HONEYCOMB_PASSWORD=xxx python3 ...`：
后者那种一行式**会进 shell 历史**。

只用标准库，不装任何依赖。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ZONES = ["生活", "工作", "学习", "健康"]

# (项目, 分区, [任务…])
PROJECTS = [
    ("做家务",     "生活", ["洗碗", "拖地板", "扔垃圾"]),
    ("做饭",       "生活", ["备菜", "煮晚饭"]),
    ("做报表",     "工作", ["拉上月数据", "对账", "写结论"]),
    ("客户跟进",   "工作", ["回邮件", "汇总需求"]),
    ("周会准备",   "工作", ["写提纲"]),
    ("读书",       "学习", ["读《深度工作》", "做读书笔记"]),
    ("学吉他",     "学习", ["练音阶", "练一首完整的"]),
    ("跑步",       "健康", ["热身", "五公里"]),
    ("睡眠调整",   "健康", ["十一点前上床"]),
]

# ── 大盘（--big）：在小盘之上再铺到 10 分区 / 40 项目 ────────────────────
#
# 为什么要有这一档：蜂巢的布局是**扇区按项目数分角度**，项目一多才看得出
# 咬合、色阶、热度分圈对不对。四个分区九个项目那一档太舒服了，压不出问题。
# 分布**故意不均**（工作 8 个、社交/旅行各 2 个），因为真实的人就是这样，
# 而窄扇区正是当初挖出空洞那个 bug 的温床。
BIG_ZONES = ZONES + ["家庭", "财务", "创作", "社交", "旅行", "杂务"]

BIG_PROJECTS = PROJECTS + [
    # 工作（共 8）
    ("季度复盘",   "工作", ["拉数据", "写材料", "对齐目标"]),
    ("招聘面试",   "工作", ["筛简历", "约时间"]),
    ("线上事故复盘", "工作", ["写时间线", "定改进项"]),
    ("产品需求评审", "工作", ["读需求", "列风险"]),
    ("同事一对一", "工作", ["准备问题"]),
    # 生活（共 6）
    ("买菜",       "生活", ["列清单", "去超市"]),
    ("换季衣物",   "生活", ["换季", "捐旧衣"]),
    ("修东西",     "生活", ["换灯泡", "通下水"]),
    ("养绿植",     "生活", ["浇水", "换盆"]),
    # 学习（共 5）
    ("学日语",     "学习", ["背单词", "看一集生肉"]),
    ("刷算法",     "学习", ["每天一题"]),
    ("看公开课",   "学习", ["线性代数第 3 讲"]),
    # 健康（共 4）
    ("力量训练",   "健康", ["深蹲", "硬拉"]),
    ("体检随访",   "健康", ["约号"]),
    # 家庭（4）
    ("陪孩子写作业", "家庭", ["数学", "语文"]),
    ("给爸妈打电话", "家庭", ["周日晚上"]),
    ("家庭出游计划", "家庭", ["选目的地", "订住处"]),
    ("修理老照片",   "家庭", ["扫描", "上色"]),
    # 财务（3）
    ("记账",       "财务", ["录本周支出", "对信用卡"]),
    ("报税",       "财务", ["收集凭证"]),
    ("看年报",     "财务", ["读一家公司"]),
    # 创作（4）
    ("写博客",     "创作", ["列提纲", "写初稿", "配图"]),
    ("剪视频",     "创作", ["粗剪", "配字幕"]),
    ("练摄影",     "创作", ["拍一组街景"]),
    ("做开源项目", "创作", ["修 issue", "写 README"]),
    # 社交（2）
    ("约朋友聚餐", "社交", ["定日子"]),
    ("回消息",     "社交", ["清未读"]),
    # 旅行（2）
    ("周末短途",   "旅行", ["查路线", "订票"]),
    ("办签证",     "旅行", ["拍照片", "填表"]),
    # 杂务（2）
    ("续保",       "杂务", ["比价"]),
    ("清邮箱",     "杂务", ["退订广告"]),
]

# 大盘的历史：热度要**拉开**，不然四十个项目一样浓，看不出"越热越靠中心"。
# 前几条刻意堆高，后面稀疏，最后一批一次都没做过（热度 0，会被推到最外圈）。
BIG_SESSIONS = [
    ("季度复盘",   "写材料",   55, 8),
    ("写博客",     "写初稿",   45, 7),
    ("刷算法",     "每天一题", 25, 11),
    ("力量训练",   "深蹲",     40, 6),
    ("学日语",     "背单词",   15, 10),
    ("买菜",       "去超市",   30, 5),
    ("记账",       "录本周支出", 12, 6),
    ("陪孩子写作业", "数学",   35, 7),
    ("剪视频",     "粗剪",     60, 3),
    ("招聘面试",   "筛简历",   20, 4),
    ("换季衣物",   "换季",     50, 2),
    ("给爸妈打电话", "周日晚上", 18, 3),
    ("看公开课",   "线性代数第 3 讲", 45, 2),
    ("做开源项目", "修 issue", 40, 4),
    ("回消息",     "清未读",   10, 5),
    ("周末短途",   "查路线",   20, 1),
    ("产品需求评审", "读需求",  30, 2),
    ("修东西",     "换灯泡",   15, 1),
]

# 大盘还要**已完成的任务**：蜂巢的热度数的是"最近完成了几条"，不是"计了多少时"。
# 只补登不打勾，四十个项目热度全是 0，位置和颜色就全靠项目顺序，
# 看不出这套布局最核心的那件事——越热越靠中心、颜色越浓。
# 所以这里另外建一批"已经干完的"任务并打上勾，名字写成过去式，读起来是真的做过。
# (项目, [已完成的任务…])
BIG_DONE = [
    ("刷算法",       ["第 1 周 30 题", "第 2 周 30 题", "周赛复盘"]),
    ("季度复盘",     ["上季度复盘", "去年年度复盘"]),
    ("写博客",       ["《为什么我不用待办清单》", "《蜂巢是怎么长出来的》"]),
    ("学日语",       ["五十音过关", "N5 词汇过一遍"]),
    ("力量训练",     ["八周计划第一阶段"]),
    ("陪孩子写作业", ["上周数学卷", "上周语文卷"]),
    ("买菜",         ["上周采购"]),
    ("记账",         ["8 月对账"]),
    ("招聘面试",     ["一面 3 位"]),
    ("剪视频",       ["第 1 期成片"]),
    ("做开源项目",   ["v0.1 发布"]),
    ("回消息",       ["清空积压"]),
    ("换季衣物",     ["夏装收纳"]),
    ("给爸妈打电话", ["上周日"]),
]

# 补登用：(项目, 任务, 大致每次分钟数, 过去 14 天里做几次)
SESSIONS = [
    ("做家务", "拖地板", 35, 5),
    ("做家务", "洗碗",     12, 9),
    ("做报表", "对账",     50, 4),
    ("做报表", "拉上月数据", 25, 3),
    ("读书",   "读《深度工作》", 40, 6),
    ("学吉他", "练音阶",   20, 4),
    ("跑步",   "五公里",   30, 5),
    ("做饭",   "煮晚饭",   45, 7),
]


# 直连，不走环境里的 http_proxy：入口通常是 127.0.0.1，代理会把它变成 502。
# （踩过：机器上设了全局代理，种子脚本第一跑就 502。）
#
# cookie 罐是 v0.2 加的。v0.2 的入口有登录门（contracts/auth.gate.v1），
# 不带会话 cookie 的 /api/core/** 请求会被 302 到 /login/，
# 然后 json.loads 在登录页的 HTML 上炸掉——报错信息还完全看不出是没登录。
# 踩过一次，所以 login() 在任何请求之前先跑。
#
# 会话 cookie 默认带 Secure（deploy/.env 的 AUTH_COOKIE_SECURE=true）。浏览器把
# 127.0.0.1 / localhost 当安全来源，http 上照样回传它；标准库只在 https 上回传。
# 不照浏览器的规矩办，照 README 起的默认站点上，登录成功之后每个请求都被踢回
# /login/（2026-09-22 按 README 从零实跑撞上）。
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


class _LoopbackIsSecure(http.cookiejar.DefaultCookiePolicy):
    def return_ok_secure(self, cookie, request):
        host = urllib.parse.urlsplit(request.get_full_url()).hostname
        return host in _LOOPBACK or super().return_ok_secure(cookie, request)


_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar(_LoopbackIsSecure())),
)


def api(base: str, path: str, method: str = "GET", body: dict | None = None):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=15) as r:
            if "/login/" in urllib.parse.urlsplit(r.geturl()).path:
                raise SystemExit(
                    f"[{method} {path}] 登录过了，请求却被踢回登录页：会话 cookie 没带上。\n"
                    "cookie 默认带 Secure，只在 https 或本机地址（127.0.0.1 / localhost）上回传。\n"
                    "非本机的 http 地址：前面加 TLS；只在内网调试时才在 deploy/.env 设 AUTH_COOKIE_SECURE=false。"
                )
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise SystemExit(f"[{method} {path}] HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {url}：{e.reason}\n先确认 `docker compose up -d` 起来了。") from None


def login(base: str, password: str, username: str = "") -> None:
    """先过门。cookie 由 _OPENER 的 cookie 罐自动带到后续请求上。

    口令不从命令行传（会留在 shell 历史和 ps 输出里），只从环境变量读。
    """
    url = base.rstrip("/") + "/api/auth/login"
    body = {"username": username, "password": password} if username else {"password": password}
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=15) as r:
            if r.status != 204:
                raise SystemExit(f"登录返回 {r.status}，契约说对口令必须是 204")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise SystemExit(
                "登录失败：账号或口令不对。\n"
                "口令从环境变量 HONEYCOMB_PASSWORD 读，账号从 HONEYCOMB_USER 读（共享口令模式不设）。"
            ) from None
        raise SystemExit(f"登录 HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"连不上 {url}：{e.reason}") from None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8800", help="入口地址")
    ap.add_argument("--seed", type=int, default=20260914, help="随机种子，保证可复现")
    ap.add_argument("--big", action="store_true",
                    help="大盘：10 分区 / 40 项目（在小盘之上加，幂等，可在已有数据上直接跑）")
    args = ap.parse_args()
    rnd = random.Random(args.seed)
    zones = BIG_ZONES if args.big else ZONES
    projects = BIG_PROJECTS if args.big else PROJECTS
    sessions = SESSIONS + BIG_SESSIONS if args.big else SESSIONS
    done_sets = BIG_DONE if args.big else []

    password = os.environ.get("HONEYCOMB_PASSWORD", "")
    if not password:
        raise SystemExit(
            "缺 HONEYCOMB_PASSWORD。\n"
            "v0.2 的入口有登录门，种子脚本要先登录才能写数据：\n"
            "    read -rsp '口令: ' HONEYCOMB_PASSWORD && export HONEYCOMB_PASSWORD\n"
            "    python3 seed/seed_demo.py\n"
            "不接受空口令——auth 服务对空口令返回 401，这里提前说清楚，\n"
            "比让你去猜一个 302 到登录页的 JSON 解析错误强。"
        )
    login(args.base, password, os.environ.get("HONEYCOMB_USER", "").strip())

    tree = api(args.base, "/api/core/views/tree")
    zone_id = {z["name"]: z["id"] for z in tree.get("zones", [])}
    proj_id = {p["name"]: p["id"] for p in tree.get("projects", [])}

    for name in zones:
        if name in zone_id:
            continue
        zone_id[name] = api(args.base, "/api/core/planner/zones", "POST", {"name": name})["id"]
        print("建分区", name)

    task_id: dict[tuple[str, str], str] = {}
    for pname, zname, tasks in projects:
        if pname not in proj_id:
            proj_id[pname] = api(args.base, "/api/core/planner/projects", "POST",
                                 {"zoneId": zone_id[zname], "name": pname})["id"]
            print("  建项目", pname)
        existing = {t["name"]: t["id"]
                    for p in api(args.base, "/api/core/views/tree").get("projects", [])
                    if p["id"] == proj_id[pname] for t in p.get("tasks", [])}
        for tname in tasks:
            if tname in existing:
                task_id[(pname, tname)] = existing[tname]
                continue
            tid = api(args.base, "/api/core/planner/tasks", "POST",
                      {"projectId": proj_id[pname], "name": tname})["id"]
            task_id[(pname, tname)] = tid
            print("    建任务", tname)

    # 已完成的任务：建出来再打勾。打勾走的是正式 CRUD（PATCH done=true），
    # 写进审计流的就是蜂巢算热度要读的那条记录，不是另造一份"假热度"。
    done_n = 0
    for pname, tasks in done_sets:
        pid = proj_id.get(pname)
        if not pid:
            continue
        existing = {t["name"]: t
                    for p in api(args.base, "/api/core/views/tree").get("projects", [])
                    if p["id"] == pid for t in p.get("tasks", [])}
        for tname in tasks:
            hit = existing.get(tname)
            if hit and hit.get("done"):
                continue
            tid = hit["id"] if hit else api(args.base, "/api/core/planner/tasks", "POST",
                                            {"projectId": pid, "name": tname})["id"]
            api(args.base, "/api/core/planner/tasks/" + tid, "PATCH", {"done": True})
            done_n += 1
    if done_sets:
        print(f"打勾 {done_n} 条已完成任务（蜂巢热度就是数这个）")

    # 过去 14 天的"已经干过"。补登是契约里的正式入口，写进的是真事件，
    # 和手动计时出来的段落同一种东西 —— 演示数据也就不会是"假的一层皮"。
    now = datetime.now(timezone.utc).astimezone()
    made = 0
    for pname, tname, minutes, times in sessions:
        tid = task_id.get((pname, tname))
        if not tid:
            continue
        for _ in range(times):
            day = rnd.randint(0, 13)
            start = (now - timedelta(days=day)).replace(
                hour=rnd.randint(8, 21), minute=rnd.choice([0, 10, 20, 30, 40, 50]),
                second=0, microsecond=0)
            dur = int(minutes * rnd.uniform(0.6, 1.4)) * 60
            # 补登只收"已经发生过的"：day=0 抽到 21 点、而现在才下午，
            # 段落尾巴就落到未来，后端直接 400。整段往前挪整天，不改时刻
            # ——挪时刻会把"这个人晚上干活"的作息也一起抹平。
            while start + timedelta(seconds=dur) > now:
                start -= timedelta(days=1)
            api(args.base, "/api/core/timer/backfill", "POST",
                {"taskId": tid, "startAt": start.isoformat(), "durationSeconds": dur})
            made += 1
    # 措辞要准：这里数的是**投出去的**补登请求数，不是新增的记录数。
    # 重跑时同样会投 43 条，但服务端按 dedupe 键去重，events 总数不变
    # （实测：跑两遍 events total 都是 43，不是 86）。
    # 原来写"补登 N 段历史记录"，重跑的人会以为自己把数据翻倍了。
    print(f"投了 {made} 条补登（重复的由服务端按 dedupe 去重，不会翻倍）")
    print("完成。打开", args.base, "看看。")


if __name__ == "__main__":
    main()
