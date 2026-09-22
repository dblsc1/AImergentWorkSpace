#!/usr/bin/env python3
"""HoneyComb 模块安装器 —— 契约文件就是它的输入。

## 它解决什么

模块之间**只通过契约 id 耦合**。nexus-core 不知道有没有 ring，ring 不知道
auth 是真账号系统还是一个 30 行的占位件。于是"装哪几个模块"这件事可以自动算：

    你选的模块 → 读它们的 consumes → 谁 provides 这些 id → 递归

缺的依赖有三种去处，按顺序找：
  1. 某个模块 provides 它        → 把那个模块也装上
  2. contracts/<id>/contract.md  → 纯规范文档（比如事件信封），不需要起服务
  3. contracts/<id>/stub/        → 有占位实现，起占位件
都没有 → **硬失败并列出缺了哪些 id**。不许静默跳过：一个依赖悬空的安装
比装不上更糟，因为它会在运行时以奇怪的方式坏掉。

## 依赖分层（有意为之）

    list / plan / doctor   零第三方依赖（只读 contract.md，正则够了）
    add                    需要 pyyaml（要解析 module.yaml 的嵌套结构）

默认组装（nexus-core + auth 占位件）已经有一份手写好的
`deploy/docker-compose.yml`，**不需要跑本脚本**。只有要换模块组合时才用 add。
这样"拉下来就能跑"不依赖任何 pip install。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"
CONTRACTS = ROOT / "contracts"
OUT = ROOT / "deploy" / "generated"


# ── 契约 id ────────────────────────────────────────────────────────
def norm_id(raw: str) -> str:
    """归一化契约 id。

    历史上两种写法并存：`yq-event/v1`（斜杠）和 `nexus-core.views.tree.v1`（点号）。
    目录名统一用点号，所以这里把斜杠折成点号。**两种写法都要认**——
    真实仓库里就是混着的，安装器不该因为风格不统一而解析失败。
    """
    return raw.strip().strip("`").replace("/", ".")


FENCE = re.compile(r"^```+\s*ya?ml\s*$", re.I)
FENCE_END = re.compile(r"^```+\s*$")
ID_LINE = re.compile(r"^\s*-\s*id:\s*(\S+)")
TOP_KEY = re.compile(r"^([a-zA-Z_][\w-]*):\s*$")


def read_contract_index(md: Path) -> tuple[list[str], list[str]]:
    """从 contract.md 里抽 provides / consumes 的 id 列表。

    只认**第一个含 provides:/consumes: 的 ```yaml 围栏块**。
    不做完整 YAML 解析 —— 只要 id，正则足够，而且这样零依赖。
    """
    provides: list[str] = []
    consumes: list[str] = []
    if not md.is_file():
        return provides, consumes

    lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
    block: list[str] | None = None
    for line in lines:
        if block is None:
            if FENCE.match(line):
                block = []
            continue
        if FENCE_END.match(line):
            if any(l.startswith(("provides:", "consumes:")) for l in block):
                break          # 找到了，用这一块
            block = None       # 不是索引块，继续找
            continue
        block.append(line)
    if not block:
        return provides, consumes

    bucket: list[str] | None = None
    for line in block:
        m = TOP_KEY.match(line)
        if m:
            key = m.group(1)
            bucket = provides if key == "provides" else consumes if key == "consumes" else None
            continue
        if bucket is None:
            continue
        m = ID_LINE.match(line)
        if m:
            bucket.append(norm_id(m.group(1)))
    return provides, consumes


# ── 发现 ───────────────────────────────────────────────────────────
class Module:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.provides, self.consumes = read_contract_index(path / "module_docs" / "contract.md")
        self.manifest = path / "module.yaml"

    @property
    def installable(self) -> bool:
        return self.manifest.is_file()


def discover() -> dict[str, Module]:
    if not MODULES.is_dir():
        return {}
    return {p.name: Module(p) for p in sorted(MODULES.iterdir()) if p.is_dir()}


def spec_file(cid: str) -> Path | None:
    """纯规范文档在哪。两种布局都认：

        contracts/<id>/contract.md          目录形（可带 stub/）
        contracts/<名字>-v<N>.md            单文件形，id 写作 contracts.<名字>.v<N>

    单文件形是历史布局（design-tokens、timer-ring-visual），被 CI 与别的模块
    按路径引用着，搬家的代价比多认一种写法大。
    """
    d = CONTRACTS / cid / "contract.md"
    if d.is_file():
        return d
    name, _, ver = cid.removeprefix("contracts.").rpartition(".")
    f = CONTRACTS / f"{name}-{ver}.md"
    return f if name and f.is_file() else None


def contract_dirs() -> dict[str, Path]:
    if not CONTRACTS.is_dir():
        return {}
    return {p.name: p for p in sorted(CONTRACTS.iterdir()) if p.is_dir()}


# ── 解析 ───────────────────────────────────────────────────────────
class Unresolved(Exception):
    pass


def resolve(selected: list[str]) -> dict:
    mods = discover()
    contracts = contract_dirs()

    unknown = [s for s in selected if s not in mods]
    if unknown:
        raise Unresolved(
            "没有这些模块：" + ", ".join(unknown)
            + "\n可用：" + (", ".join(mods) or "（空）")
        )

    provider: dict[str, str] = {}
    for name, m in mods.items():
        for cid in m.provides:
            # 同一个 id 被多个模块 provides 是可以的（可替换实现）；
            # 先到先得，并在报告里点出来，让人知道选了哪个
            provider.setdefault(cid, name)

    chosen: list[str] = []
    stubs: list[str] = []
    specs: list[str] = []
    missing: list[tuple[str, str]] = []   # (契约 id, 谁要的)

    queue = list(selected)
    while queue:
        name = queue.pop(0)
        if name in chosen:
            continue
        chosen.append(name)
        for cid in mods[name].consumes:
            if cid in provider:
                dep = provider[cid]
                if dep not in chosen and dep not in queue:
                    queue.append(dep)
            elif (CONTRACTS / cid / "stub").is_dir():
                if cid not in stubs:
                    stubs.append(cid)
            elif spec_file(cid):
                if cid not in specs:
                    specs.append(cid)          # 纯规范，不起服务
            else:
                missing.append((cid, name))

    if missing:
        detail = "\n".join(f"  {cid}  ← {who} 需要" for cid, who in missing)
        raise Unresolved(
            "以下契约无人提供，装不了：\n" + detail
            + "\n\n三种解法：把提供它的模块也放进 modules/；"
            "或在 contracts/<id>/ 放 contract.md（纯规范）；"
            "或放 stub/（占位实现）。\n"
            "**不会静默跳过**——依赖悬空的安装会在运行时以奇怪的方式坏掉。"
        )

    return {
        "selected": selected,
        "modules": chosen,
        "stubs": stubs,
        "specs": specs,
        "contracts_available": sorted(contracts),
    }


# ── 命令 ───────────────────────────────────────────────────────────
def cmd_list() -> int:
    mods = discover()
    if not mods:
        print("modules/ 下没有模块。")
        return 1
    print("模块：")
    for name, m in mods.items():
        flag = "" if m.installable else "   ⚠ 缺 module.yaml，只能当依赖被引用，不能直接装"
        print(f"  {name}{flag}")
        for cid in m.provides:
            print(f"      provides  {cid}")
        for cid in m.consumes:
            print(f"      consumes  {cid}")
    cds = contract_dirs()
    if cds:
        print("\n契约：")
        for cid, p in cds.items():
            kind = []
            if (p / "contract.md").is_file():
                kind.append("规范")
            if (p / "stub").is_dir():
                kind.append("占位实现")
            print(f"  {cid}  [{'+'.join(kind) or '空'}]")
    for f in sorted(CONTRACTS.glob("*-v*.md")):      # 单文件形，见 spec_file
        name, _, ver = f.stem.rpartition("-")
        print(f"  contracts.{name}.{ver}  [规范·单文件]")
    return 0


def _print_plan(plan: dict) -> None:
    print("要装的模块：")
    for n in plan["modules"]:
        tag = "（你选的）" if n in plan["selected"] else "（依赖拉进来的）"
        print(f"  {n} {tag}")
    if plan["stubs"]:
        print("\n用占位实现满足的契约：")
        for c in plan["stubs"]:
            print(f"  {c}   ← 占位件，不是生产实现；换掉它组装层零改动")
    if plan["specs"]:
        print("\n由纯规范文档满足的契约（不起服务）：")
        for c in plan["specs"]:
            print(f"  {c}")


def cmd_plan(args: list[str]) -> int:
    if not args:
        print("用法: install.sh plan <模块>...", file=sys.stderr)
        return 2
    _print_plan(resolve(args))
    return 0


def cmd_add(args: list[str]) -> int:
    if not args:
        print("用法: install.sh add <模块>...", file=sys.stderr)
        return 2
    try:
        import yaml  # noqa: F401
    except ImportError:
        print(
            "add 需要 pyyaml（要解析 module.yaml 的嵌套结构）：pip install pyyaml\n"
            "\n只想看依赖怎么解的话用 plan / list，那两个零依赖。\n"
            "只想跑默认组装（nexus-core + auth 占位件）的话根本不用本脚本：\n"
            "    cd deploy && cp .env.example .env && 编辑口令 && docker compose up -d",
            file=sys.stderr,
        )
        return 3

    plan = resolve(args)
    from generate import emit          # 同目录，延迟导入：list/plan 不该为它付依赖
    OUT.mkdir(parents=True, exist_ok=True)
    written, meta = emit(ROOT, plan, OUT)
    plan.update(meta)
    _print_plan(plan)
    if plan.get("auto_included"):
        for cid in plan["auto_included"]:
            print(f"\n⚠ 自动装上了 {cid} 的占位件：有路由声明 gated: true，"
                  f"没有门就会 fail-open。\n"
                  f"   这是个占位实现，不是生产账号系统 —— 见 contracts/{cid}/contract.md")
    print("\n生成：")
    for p in written:
        print(f"  {p.relative_to(ROOT)}")
    (OUT / "installed.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  {(OUT / 'installed.json').relative_to(ROOT)}")
    return 0


def cmd_doctor() -> int:
    """核对已装清单与当前文件是否还对得上。

    典型故障：模块被删了、契约 id 改了、stub 目录没了。
    这些都会让一个曾经能跑的安装变成运行时的怪毛病，所以值得一条命令查。
    """
    f = OUT / "installed.json"
    if not f.is_file():
        print(f"没有 {f.relative_to(ROOT)} —— 还没用 add 装过。")
        return 1
    plan = json.loads(f.read_text(encoding="utf-8"))
    bad = 0
    for name in plan.get("modules", []):
        if not (MODULES / name).is_dir():
            print(f"❌ 模块不见了: {name}")
            bad += 1
    for cid in plan.get("stubs", []):
        if not (CONTRACTS / cid / "stub").is_dir():
            print(f"❌ 占位实现不见了: {cid}")
            bad += 1
    for cid in plan.get("specs", []):
        if not spec_file(cid):
            print(f"❌ 契约文档不见了: {cid}")
            bad += 1
    try:
        fresh = resolve(plan.get("selected", []))
    except Unresolved as e:
        print(f"❌ 按当前文件重新解析失败：\n{e}")
        return 1
    if fresh["modules"] != plan.get("modules"):
        print("⚠ 重新解析的模块集合与已装清单不同：")
        print(f"    已装: {plan.get('modules')}")
        print(f"    现在: {fresh['modules']}")
        bad += 1
    bad += _drift()
    print("✅ 一致" if not bad else f"\n{bad} 处不一致")
    return 0 if not bad else 1


def _drift() -> int:
    """手写的默认组装 vs 生成产物，服务集合是否漂移。

    为什么要查：仓里同时存在两份 compose ——
      deploy/docker-compose.yml      手写，battle-tested，零依赖开箱跑
      deploy/generated/...           install.sh add 的产出
    只留生成版就破了"拉下来就能跑，不需要 pip install"；
    只留手写版就破了"契约是安装器的输入"。所以两份都要，
    但必须有人盯着它们别悄悄分家 —— 就是这个函数。

    只比服务名集合，不比细节。细节差异是正常的（手写那份带 container_name
    之类的本机便利），服务**少了一个**才是真问题。
    """
    hand = ROOT / "deploy" / "docker-compose.yml"
    gen = OUT / "docker-compose.yml"
    if not (hand.is_file() and gen.is_file()):
        return 0
    try:
        import yaml
    except ImportError:
        print("ℹ 跳过漂移检测（需 pyyaml）")
        return 0
    def svcs(p):
        try:
            return set((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("services") or {})
        except Exception as e:
            print(f"⚠ 读不了 {p.relative_to(ROOT)}：{e}")
            return None
    a, b = svcs(hand), svcs(gen)
    if a is None or b is None:
        return 1
    if a == b:
        return 0
    print("⚠ 默认组装与生成产物的服务集合不一致：")
    if b - a:
        print(f"    手写那份缺: {sorted(b - a)}   ← 默认组装会少起这些服务")
    if a - b:
        print(f"    生成那份缺: {sorted(a - b)}")
    print("    → 改清单后记得同步手写那份，或把它当快照重新生成一遍")
    return 1


USAGE = """HoneyComb 模块安装器

  install.sh list                  列出模块与契约（零依赖）
  install.sh plan <模块>...        看依赖怎么解，不写文件（零依赖）
  install.sh add  <模块>...        解依赖并生成 compose/nginx（需 pyyaml）
  install.sh doctor                核对已装清单与当前文件

默认组装不用本脚本：cd deploy && docker compose up -d
"""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "list":
            return cmd_list()
        if cmd == "plan":
            return cmd_plan(args)
        if cmd == "add":
            return cmd_add(args)
        if cmd == "doctor":
            return cmd_doctor()
    except Unresolved as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"未知命令: {cmd}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
