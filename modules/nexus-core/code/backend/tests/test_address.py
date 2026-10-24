"""位置编号 ``address``（契约 v2.39 一）：基础编号、撞号、拼接、解析、views/tree 带出。"""

from __future__ import annotations

import pytest

from app.modules.views import address as A

API = "/api/core"
PLANNER = f"{API}/planner"


# ------------------------------------------------------------------ 基础编号


@pytest.mark.parametrize("name,limit,want", [
    ("教育", 2, "jy"),
    ("结构市场", 4, "jgsc"),
    ("课程代码", 4, "kcdm"),
    ("繁體計劃", 4, "ftjh"),                    # 繁体照样每字取首字母
    ("HoneyComb", 4, "h"),                       # 驼峰是一个词
    ("camelCaseName", 4, "c"),
    ("2024计划", 4, "2jh"),                      # 数字词取首位数字
    ("2024-10-24", 4, "212"),
    ("hello world foo bar baz", 4, "hwfb"),      # 取够即止
    ("测试CS2", 4, "csc"),                       # 汉字截断前面的词；CS2 是一个词
    ("abc测d", 4, "acd"),
    ("A_B-C", 4, "abc"),                         # 标点只当分隔
    ("a1", 4, "a"),
    ("12ab", 4, "1"),
    ("Café au lait", 4, "cal"),
    ("Éclair", 4, "é"),                          # 带重音的拉丁字母算字母，小写
    ("日本語ひらがな한글", 4, "rby"),               # 汉字取拼音，假名 / 韩文只当分隔
    ("教育部门", 2, "jy"),                        # 分区只取 2 位
    ("教", 4, "j"),                               # 不够不补
    ("", 2, "x"),
    ("   ", 4, "x"),
    ("!!! 🙂", 4, "x"),
    ("🙂教🙂育", 4, "jy"),
])
def test_base(name, limit, want):
    assert A.base(name, limit) == want


def test_system_names():
    assert A.base("收件箱", 2) == "sj" and A.base("收件箱", 4) == "sjx"
    assert A.base("未分类", 4) == "wfl"


# ------------------------------------------------------------------ 撞号


def test_collision_suffix_in_creation_order():
    got = A.assign([("c", "测试", 3), ("a", "测试", 1), ("b", "测试", 2)], 4)
    assert got == {"a": "cs", "b": "cs2", "c": "cs3"}


def test_literal_cs2_vs_second_test_second_created_first():
    """「CS 2」本身的基础编号就是 cs2：先建的占了 cs2，第二个「测试」就顺延到 cs3，不撞。"""
    got = A.assign([("p1", "C S 2", 1), ("p2", "测试", 2), ("p3", "测试", 3)], 4)
    assert got == {"p1": "cs2", "p2": "cs", "p3": "cs3"}


def test_literal_cs2_vs_second_test_created_after():
    got = A.assign([("p1", "测试", 1), ("p2", "测试", 2), ("p3", "C S 2", 3)], 4)
    assert got == {"p1": "cs", "p2": "cs2", "p3": "cs22"}  # 基础 cs2 已被第二个「测试」占了 → 基础 + 2


def test_collision_is_case_insensitive():
    assert A.assign([("1", "É", 1), ("2", "é", 2)], 4) == {"1": "é", "2": "é2"}


def test_tie_broken_by_id():
    assert A.assign([("b", "x1", 0), ("a", "x1", 0)], 4) == {"a": "x", "b": "x2"}
    assert A.assign([("b", "课程", 5), ("a", "课程", 5)], 4) == {"a": "kc", "b": "kc2"}


def test_empty_names_collide_as_x():
    assert A.assign([("a", "!!!", 1), ("b", "???", 2)], 4) == {"a": "x", "b": "x2"}


# ------------------------------------------------------------------ 拼接 / 层级 / 解析

Z = [{"id": "z1", "name": "教育"}, {"id": "z2", "name": "教育"}]
P = [{"id": "p1", "zoneId": "z1", "name": "结构市场"}, {"id": "p2", "zoneId": "z1", "name": "结构市场"},
     {"id": "p3", "zoneId": "z2", "name": "结构市场"}]
T = [{"id": "t1", "projectId": "p1", "name": "课程代码"}, {"id": "t2", "projectId": "p1", "name": "课程代码"},
     {"id": "t3", "projectId": "p2", "name": "课程代码"}]
CREATED = {"zones": {"z1": "1", "z2": "2"}, "projects": {"p1": "1", "p2": "2", "p3": "3"}, "tasks": {"t1": "1", "t2": "2", "t3": "3"}}


def test_build_scopes_collisions_per_level():
    a = A.build(Z, P, T, CREATED)
    assert a.zones == {"z1": "jy", "z2": "jy2"}
    # 项目的同级 = 同一分区：p3 在另一个分区，不被 p1 / p2 占用
    assert a.projects == {"p1": "jy-jgsc", "p2": "jy-jgsc2", "p3": "jy2-jgsc"}
    # 任务的同级 = 同一项目
    assert a.tasks == {"t1": "jy-jgsc-kcdm", "t2": "jy-jgsc-kcdm2", "t3": "jy-jgsc2-kcdm"}
    assert all("-" not in part for addr in a.tasks.values() for part in [addr.split("-")[2]])


def test_build_without_creation_info_falls_back_to_id():
    a = A.build(Z, P, T, {})
    assert a.zones == {"z1": "jy", "z2": "jy2"}


def test_resolve():
    a = A.build(Z, P, T, CREATED)
    assert A.resolve(a, "jy") == ("zone", "z1")
    assert A.resolve(a, "  JY-JGSC2 ") == ("project", "p2")      # 去空白、不分大小写
    assert A.resolve(a, "jy-jgsc-kcdm2") == ("task", "t2")
    assert A.resolve(a, "jy-jg") is None                          # 不做前缀匹配
    assert A.resolve(a, "jy-jgsc-kcdm-x") is None
    assert A.resolve(a, "") is None and A.resolve(a, None) is None
    assert A.resolve(a, "nope") is None


def test_resolve_case_insensitive_for_accented():
    a = A.build([{"id": "z", "name": "Éa"}], [], [], {})
    assert a.zones == {"z": "é"}
    assert A.resolve(a, "É") == ("zone", "z")


def test_target_prefers_task():
    a = A.build(Z, P, T, CREATED)
    assert a.target("t1", "p1") == "jy-jgsc-kcdm" and a.target(None, "p1") == "jy-jgsc"
    assert a.target(None, None) is None and a.target("gone", "gone") is None


# ------------------------------------------------------------------ views/tree


def _post(client, path, **body):
    r = client.post(f"{PLANNER}/{path}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_tree_has_address_everywhere(client):
    zone = _post(client, "zones", name="教育")
    project = _post(client, "projects", zoneId=zone["id"], name="结构市场")
    t1 = _post(client, "tasks", projectId=project["id"], name="课程代码")
    done = _post(client, "tasks", projectId=project["id"], name="课程代码")
    assert client.patch(f"{PLANNER}/tasks/{done['id']}", json={"done": True}).status_code == 200
    eph = _post(client, "tasks", projectId=project["id"], name="课程代码", kind="ephemeral")
    t4 = _post(client, "tasks", projectId=project["id"], name="课程代码")
    assert client.post(f"{PLANNER}/projects/{project['id']}/unclassified").status_code in (200, 201)

    tree = client.get(f"{API}/views/tree").json()  # 默认不含临时任务，但临时任务照样占号
    z = next(x for x in tree["zones"] if x["id"] == zone["id"])
    p = next(x for x in tree["projects"] if x["id"] == project["id"])
    assert z["address"] == "jy" and p["address"] == "jy-jgsc"
    by_id = {t["id"]: t["address"] for t in p["tasks"]}
    assert by_id == {t1["id"]: "jy-jgsc-kcdm", done["id"]: "jy-jgsc-kcdm2", t4["id"]: "jy-jgsc-kcdm4"}
    assert eph["id"] not in by_id
    assert p["unclassifiedTaskId"] and p["unclassifiedAddress"] == "jy-jgsc-wfl"
    full = client.get(f"{API}/views/tree", params={"includeEphemeral": "true"}).json()
    fp = next(x for x in full["projects"] if x["id"] == project["id"])
    assert {t["id"]: t["address"] for t in fp["tasks"]}[eph["id"]] == "jy-jgsc-kcdm3"


def test_tree_inbox_and_every_node(client, seeded):
    from app.modules.planner import inbox  # noqa: PLC0415

    inbox.ensure()
    tree = client.get(f"{API}/views/tree").json()
    assert all(z["address"] for z in tree["zones"])
    assert all(p["address"] and all(t["address"] for t in p["tasks"]) for p in tree["projects"])
    inbox_p = next(p for p in tree["projects"] if p["id"] == "p_inbox")
    assert inbox_p["address"].split("-")[1] == "sjx"


def test_project_without_zone_not_resolvable():
    a = A.build([{"id": "z1", "name": "教育"}], [{"id": "p1", "zoneId": None, "name": "孤儿"}, {"id": "p2", "zoneId": "gone", "name": "孤儿"}],
                [{"id": "t1", "projectId": "p1", "name": "任务"}], {})
    assert A.resolve(a, "x-gr") is None and A.resolve(a, "x-gr2") is None and A.resolve(a, "x-gr-rw") is None
    assert A.resolve(a, "jy") == ("zone", "z1")


def test_unreadable_names_collide_as_x_at_every_level():
    a = A.build([{"id": "z1", "name": "!!"}, {"id": "z2", "name": "??"}], [{"id": "p1", "zoneId": "z1", "name": "!"}, {"id": "p2", "zoneId": "z1", "name": "?"}],
                [{"id": "t1", "projectId": "p1", "name": "!"}, {"id": "t2", "projectId": "p1", "name": "?"}], {})
    assert a.zones == {"z1": "x", "z2": "x2"} and a.projects == {"p1": "x-x", "p2": "x-x2"} and a.tasks == {"t1": "x-x-x", "t2": "x-x-x2"}
