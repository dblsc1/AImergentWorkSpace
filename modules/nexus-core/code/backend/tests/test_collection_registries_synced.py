"""集合名登记表必须互相一致。

## 这是什么

仓里有**两份手打的 MongoDB 集合名清单**，用途不同：

    conftest._COLLECTIONS        每个测试跑完照它清空表，保证测试之间互不污染
    test_export._COLLECTIONS     导出前后照它数行数，用来证明"导出没改任何数据"

两份都必须覆盖 app 实际用到的全部集合，但**没有任何机制保证这件事** ——
全靠加新集合的人记得每一处都改。

## 漏掉的后果，按用途各不相同，而且都隐蔽

    conftest 漏     那个集合的数据**跨测试累积**。"这条记录是本用例写的"不再成立，
                    症状是别的用例莫名多出几条 —— 最难查的那一种。
    test_export 漏  那个集合上的写入**不在证明范围内**。导出按契约是只读的，
                    本文件靠计数快照证明它；清单里缺一张表，哪天改动让导出
                    顺手写了条审计流水，测试照样绿。

2026-09-17 实测：`test_export` 那份确实漏了 `planner_audit`，而 `conftest` 那份有
（且旁边就写着"新增集合必须同批加进这张表"）。**漏的那份不会报错，只会静默少守一块。**

## 为什么是静态断言，不连库

不看"库里现在有哪些集合"—— 那会被历史遗留集合污染成假红。
只读源码里写死了哪些名字，两份比对。这样它在任何环境下结论都一样。

## 为什么不去自动推导"app 用了哪些集合"

试过，推不干净。集合身份是**运行时字符串**：通用 repo 层把集合名当参数收，
名字以裸字符串实参散落在 service 层（`_find_one("zones", id)`），
不以 `db["zones"]` 的形状出现。静态提取能覆盖大部分但有盲区，
做成硬断言就会误报 —— 而一条天天误报的断言会被注释掉。
所以这里只守**可以零误报机械判定的那一半**：两份表必须相等。
"""

from __future__ import annotations

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_TUPLE = re.compile(r"_COLLECTIONS\s*=\s*\((.*?)\)", re.S)
_NAME = re.compile(r"""["']([a-z_]+)["']""")


def _registry(path: Path) -> set[str]:
    m = _TUPLE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name} 里找不到 _COLLECTIONS —— 改名了？改了就同步改本测试"
    return set(_NAME.findall(m.group(1)))


def test_collection_registries_agree():
    conftest = _registry(_BACKEND / "conftest.py")
    export = _registry(_BACKEND / "tests" / "test_export.py")

    assert conftest, "conftest._COLLECTIONS 解析出来是空的"
    assert export, "test_export._COLLECTIONS 解析出来是空的"

    only_conftest = sorted(conftest - export)
    only_export = sorted(export - conftest)
    assert not only_conftest and not only_export, (
        "两份集合名登记表不一致 —— 新增集合时漏改了其中一处。\n"
        f"  只在 conftest 里: {only_conftest or '无'}\n"
        f"  只在 test_export 里: {only_export or '无'}\n"
        "两份都要覆盖 app 用到的全部集合：conftest 少一个会让那张表跨测试累积；\n"
        "test_export 少一个会让那张表上的写入不在'导出是只读的'这个证明范围内。"
    )
