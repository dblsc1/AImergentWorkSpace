#!/usr/bin/env python3
"""A2/A3 判据（2026-08-08 任务单）：ring 的 tokens-fallback 兜底块必须与
``contracts/design-tokens-v1.md`` §3 全表逐字节一致，且兜底块之外禁止裸 hex。

    python3 code/frontend/tests/check_tokens_fallback.py

diff 的对象是**契约本身**（唯一事实源），不是设计稿附件——契约 §3.4 明文把
字体/间距/圆角/触控/动效的具体值"抄设计稿 v1 tokens.css，不复述"，所以那五类
值改而只对设计稿附件的 tokens.css 做二次校验，颜色（§3.1/3.2/3.3）永远对
contracts/design-tokens-v1.md 校验。

跨仓路径（ring 是独立 git 仓，contracts/ 在框架根仓）在**可选**的意义上使用：
框架根仓不在（例如 ring 被单独 clone 出来测）时响亮 skip、exit 0，不是拿假数据
硬撑出一个"通过"（关键路径缺文件必须 die / 可选件缺失必须打印已跳过——
这里判定为可选件，因为 ring 单独存在时本来就没有能力核对跨仓契约）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent
CSS_FILE = FRONTEND_DIR / "ring.css"
CONTRACT_FILE = FRONTEND_DIR / "../../../../contracts/design-tokens-v1.md"
DESIGN_TOKENS_FILE = FRONTEND_DIR / "../../../../contracts/design/tokens.css"

BEGIN_MARK = "/* tokens-fallback:begin */"
END_MARK = "/* tokens-fallback:end */"


def skip(message: str) -> int:
    print(f"⏭  已跳过 tokens-fallback 校验：{message}")
    return 0


def fail(message: str) -> None:
    print(f"  ❌ {message}")


def ok(message: str) -> None:
    print(f"  ✅ {message}")


# ── 从 ring.css 里抠出兜底块，与其余部分分开（A2 要单独查兜底块外面）───────
def extract_fallback_block(css_text: str) -> tuple[str, str]:
    if BEGIN_MARK not in css_text or END_MARK not in css_text:
        raise ValueError(f"{CSS_FILE} 里找不到 {BEGIN_MARK}/{END_MARK} 哨兵")
    before, rest = css_text.split(BEGIN_MARK, 1)
    inside, after = rest.split(END_MARK, 1)
    return inside, before + after


# ── 解析 CSS 声明块：只认本文件自己生成的这六个固定选择器（A3 的比较对象是
#    "已知形状"，不是通用 CSS 解析器）────────────────────────────────────
_BLOCK_SELECTORS = {
    "light_base": r':root,\s*html\[data-theme="light"\]\s*\{(.*?)\}',
    "dark_base": r'html\[data-theme="dark"\]\s*\{(.*?)\}',
    "violet_light": r'html\[data-accent="violet"\]\s*\{(.*?)\}',
    "violet_dark": r'html\[data-theme="dark"\]\[data-accent="violet"\]\s*\{(.*?)\}',
    "amber_light": r'html\[data-accent="amber"\]\s*\{(.*?)\}',
    "amber_dark": r'html\[data-theme="dark"\]\[data-accent="amber"\]\s*\{(.*?)\}',
}
_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+?)\s*;")


def parse_declared_blocks(fallback_css: str) -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    for name, pattern in _BLOCK_SELECTORS.items():
        m = re.search(pattern, fallback_css, re.S)
        if not m:
            raise ValueError(f"兜底块里找不到预期的选择器块：{name}（正则 {pattern!r} 没命中）")
        blocks[name] = {k: v.strip() for k, v in _DECL_RE.findall(m.group(1))}
    return blocks


def parse_plain_root_block(fallback_css: str) -> dict[str, str]:
    """取字体/间距/圆角/触控/动效那个独立的纯 ``:root { ... }`` 块（不带任何
    data-theme/data-accent 属性选择器的那一个——契约 §3.4 委托给设计稿的部分）。"""
    for m in re.finditer(r":root\s*\{(.*?)\}", fallback_css, re.S):
        body = m.group(1)
        if "--font-body" in body:
            return {k: v.strip() for k, v in _DECL_RE.findall(body)}
    raise ValueError("兜底块里找不到字体/间距用的纯 :root { … } 块")


# ── 解析契约 contracts/design-tokens-v1.md 的 §3.1/3.2/3.3 表 ──────────
_ROW_23_RE = re.compile(r"^\|\s*`(--[\w-]+)`\s*\|\s*`([^`]*)`\s*\|\s*`([^`]*)`\s*\|", re.M)
_ROW_33_RE = re.compile(
    r"^\|\s*(?:`([a-z]+)`[^|]*)?\|\s*`(--[\w-]+)`\s*\|\s*`([^`]*)`\s*\|\s*`([^`]*)`\s*\|", re.M
)


def parse_contract_colors(contract_text: str) -> tuple[dict[str, tuple[str, str]], dict[str, dict[str, tuple[str, str]]]]:
    def section(name_start: str, name_end: str | None) -> str:
        start = contract_text.index(name_start)
        end = contract_text.index(name_end, start) if name_end else len(contract_text)
        return contract_text[start:end]

    sec_31_32 = section("### 3.1", "### 3.3")
    sec_33 = section("### 3.3", "### 3.4")

    neutral_semantic: dict[str, tuple[str, str]] = {}
    for token, light, dark in _ROW_23_RE.findall(sec_31_32):
        neutral_semantic[token] = (light, dark)

    presets: dict[str, dict[str, tuple[str, str]]] = {"teal": {}, "violet": {}, "amber": {}}
    current_preset = None
    for preset_name, token, light, dark in _ROW_33_RE.findall(sec_33):
        if preset_name:
            current_preset = preset_name
        if current_preset is None:
            raise ValueError(f"3.3 表第一行没有给出预设名，解析不出 {token} 属于哪个预设")
        presets[current_preset][token] = (light, dark)
    return neutral_semantic, presets


def parse_design_draft_root(tokens_css_text: str) -> dict[str, str]:
    m = re.search(r":root\s*\{(.*?)\}\s*\n@media \(prefers-reduced-motion", tokens_css_text, re.S)
    if not m:
        raise ValueError("设计稿 tokens.css 里找不到字体/间距的 :root { … } 块")
    return {k: v.strip() for k, v in _DECL_RE.findall(m.group(1))}


# ── A2：兜底块之外禁止裸 hex ──────────────────────────────────────────
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def check_no_bare_hex_outside_fallback() -> int:
    fail_count = 0
    for css_path in sorted(FRONTEND_DIR.glob("*.css")):
        text = css_path.read_text(encoding="utf-8")
        if BEGIN_MARK in text and END_MARK in text:
            _, outside = extract_fallback_block(text)
        else:
            outside = text
        hits = _HEX_RE.findall(outside)
        if hits:
            fail(f"A2：{css_path.name} 兜底块之外出现裸 hex：{sorted(set(hits))}")
            fail_count += 1
        else:
            ok(f"A2：{css_path.name} 兜底块之外无裸 hex")
    for js_path in sorted(FRONTEND_DIR.glob("*.js")):
        hits = _HEX_RE.findall(js_path.read_text(encoding="utf-8"))
        if hits:
            fail(f"A2：{js_path.name} 出现裸 hex（JS 不该有兜底块豁免）：{sorted(set(hits))}")
            fail_count += 1
        else:
            ok(f"A2：{js_path.name} 无裸 hex")
    return fail_count


def main() -> int:
    if not CSS_FILE.exists():
        print(f"❌ 关键文件缺失：{CSS_FILE}（这是本模块自己的文件，不是可选跨仓依赖，必须 die）")
        return 1
    if not CONTRACT_FILE.exists():
        return skip(
            f"跨仓依赖不可达（{CONTRACT_FILE} 不存在）——ring 可以被单独 clone "
            "出来测，那时看不到根仓的 contracts/，属于可选件缺失，不是本模块自身的缺陷。"
        )

    css_text = CSS_FILE.read_text(encoding="utf-8")
    fallback_css, _ = extract_fallback_block(css_text)
    declared = parse_declared_blocks(fallback_css)
    declared_root = parse_plain_root_block(fallback_css)

    neutral_semantic, presets = parse_contract_colors(CONTRACT_FILE.read_text(encoding="utf-8"))
    # 设计稿 tokens.css 只管 §3.4（字体/间距/圆角/触控/动效），它不在本仓里
    # （设计稿不是交付物）。缺它**只跳过 A3-3 一段**，A2 与 A3-1/A3-2 照跑 ——
    # 早先是整份 skip，那等于这个检查在本仓永远绿而从没跑过。
    design_root = (
        parse_design_draft_root(DESIGN_TOKENS_FILE.read_text(encoding="utf-8"))
        if DESIGN_TOKENS_FILE.exists()
        else None
    )

    fail_count = 0

    # A3-1：3.1/3.2 中性与语义 token，light 对 light_base，dark 对 dark_base。
    for token, (light, dark) in neutral_semantic.items():
        for mode, expected, block_name in (("light", light, "light_base"), ("dark", dark, "dark_base")):
            actual = declared[block_name].get(token)
            if actual != expected:
                fail(f"A3：{token}（{mode}）契约={expected!r}，兜底块={actual!r}")
                fail_count += 1
    if fail_count == 0:
        ok(f"A3：3.1/3.2 共 {len(neutral_semantic)} 个 token 的明暗两态与契约逐字节一致")

    # A3-2：3.3 accent 预设。teal 落在 light_base/dark_base 里（默认预设不需要
    # 单独的 data-accent 选择器）；violet/amber 各自落在专属选择器块。
    preset_block_map = {
        "teal": ("light_base", "dark_base"),
        "violet": ("violet_light", "violet_dark"),
        "amber": ("amber_light", "amber_dark"),
    }
    preset_fail = 0
    for preset, tokens in presets.items():
        light_block, dark_block = preset_block_map[preset]
        for token, (light, dark) in tokens.items():
            for mode, expected, block_name in (("light", light, light_block), ("dark", dark, dark_block)):
                actual = declared[block_name].get(token)
                if actual != expected:
                    fail(f"A3：{preset}/{token}（{mode}）契约={expected!r}，兜底块={actual!r}")
                    preset_fail += 1
    if preset_fail == 0:
        ok("A3：3.3 三套 accent 预设（teal/violet/amber）× 明暗两态与契约逐字节一致")
    fail_count += preset_fail

    # A3-3：3.4 字体/间距/圆角/触控/动效——契约委托给设计稿 tokens.css 的部分。
    root_fail = 0
    if design_root is None:
        print(f"  ⏭ A3（§3.4）：设计稿 {DESIGN_TOKENS_FILE.name} 不在本仓，这一段**未检测**")
        design_root = {}
    for token, expected in design_root.items():
        actual = declared_root.get(token)
        if actual != expected:
            fail(f"A3（§3.4）：{token} 设计稿={expected!r}，兜底块={actual!r}")
            root_fail += 1
    if root_fail == 0 and design_root:
        ok(f"A3（§3.4）：{len(design_root)} 个字体/间距/触控/动效 token 与设计稿逐字节一致")
    fail_count += root_fail

    fail_count += check_no_bare_hex_outside_fallback()

    if fail_count:
        print(f"\n❌ tokens-fallback 校验失败（{fail_count} 处不一致/违规）")
        return 1
    tail = "" if design_root else "；§3.4 未检测（设计稿不在本仓）"
    print(f"\n✅ tokens-fallback 校验通过（A2 无裸 hex + A3 兜底块与契约逐字节一致{tail}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
