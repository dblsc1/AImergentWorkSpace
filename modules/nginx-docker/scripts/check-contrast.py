#!/usr/bin/env python3
"""A1 · 对比度矩阵机械校验（design-tokens-v1.md §3 → §4 A1 的判据本体）。

    python3 code/backend/scripts/check-contrast.py

不解析 `/__cockpit/tokens.css`，也不信契约表里手写的「对比度」列数字——
那两个都可能抄错、算错或过期。本脚本只信契约表里的**颜色**（token 的 light/dark
十六进制值）与**门限**（"≥X" 那个数字），自己按 WCAG 相对亮度公式重新算一遍，
两者对不上就是红。这正是契约 §3.0 那句「估算不是判据，脚本才是」的落地。

跑法与判据：
  按「3 预设 × 明暗 = 6 态」跑一遍契约 §3 表里**出现了对比度/门限**的每一个 token
  （标了「不承诺」或没有门限数字的行——如 --plan-soft/--fact-soft/--fact-s3/
  --accent-soft、以及 §3.1 里没有对比度标注的中性色——不在校验范围内，
  它们本来就没有承诺）。任一组合实算值 < 门限即整体判红，非零退出。

对基准底（跟谁比）的判定，逐字对应契约行文，不是本脚本自己发明的规则：
  · 默认基准是 `--panel`（契约 §3 开头「面板亮/暗 … 为文本对比的基准底」）；
  · 行文里出现「于 --X」的，改用 --X 做基准（--focus 于 --bg；
    --fact-ink 于 --fact；每个预设自己的 --accent-ink 于**该预设自己的** --accent）。

可失败性：把契约表里任一门限或颜色改错，本脚本会红（已人工验证过）。
"""
import re
import sys
from pathlib import Path

HEX_RE = r"#[0-9A-Fa-f]{6}"
THRESHOLD_RE = re.compile(r"≥\s*([0-9.]+)")
BASE_OVERRIDE_RE = re.compile(r"于\s*(--[\w-]+)")


def die(msg):
    print("❌ " + msg, file=sys.stderr)
    sys.exit(1)


def find_contract():
    """从本脚本位置向上找 `contracts/design-tokens-v1.md`。

    不写死层数——脚本被挪动（比如 ring/hive 各自也想跑一份）
    时,硬编码的 `../../../../../` 会静默指向错误的文件而不报错。
    改为逐层向上找`contracts/`目录,找不到才 die,且把找过的路径打出来。
    """
    here = Path(__file__).resolve()
    tried = []
    for parent in here.parents:
        cand = parent / "contracts" / "design-tokens-v1.md"
        tried.append(str(cand))
        if cand.is_file():
            return cand
    die(
        "找不到 contracts/design-tokens-v1.md（本模块与仓库根 contracts/ 同属一棵目录树，"
        "按模块通常的挂载深度逐层向上找）。试过的路径：\n   "
        + "\n   ".join(tried)
    )


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hexcolor):
    r, g, b = hex_to_rgb(hexcolor)
    return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)


def contrast_ratio(hex_a, hex_b):
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def split_row(line):
    """markdown 表格一行 -> 去掉首尾空 cell、去反引号的 cell 列表。"""
    cells = line.strip().split("|")
    cells = [c.strip() for c in cells]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [c.replace("`", "").strip() for c in cells]


def extract_section(text, heading):
    """截出 `### {heading}` 到下一个 `##`/`###` 标题之间的正文。"""
    pat = re.compile(
        r"^###\s*" + re.escape(heading) + r".*?\n(.*?)(?=\n##|\Z)",
        re.S | re.M,
    )
    m = pat.search(text)
    if not m:
        die(f"契约里找不到「### {heading}」这一节——章节标题被改了？本脚本按标题定位，需同步。")
    return m.group(1)


TABLE_ROW_RE = re.compile(r"^\|.*(--[\w-]+).*\|$")


def parse_neutral_or_semantic(section_text):
    """解析 §3.1 / §3.2 这种「token | light | dark | (对比度) | (门限)」形状的表。

    §3.1 没有独立的「对比度」「门限」列，两者都压在最后一个文字列里
    （形如「正文（16.37 / 14.33 ≥4.5）」或「焦点环（5.34 / 7.76 于 --bg ≥3）」）；
    §3.2 有独立的两列。两种都按「取最后一格找 ≥X」「其余格找十六进制」处理，
    对两种形状都成立，不用分支。

    返回：{token: {"light": hex, "dark": hex, "threshold": float|None, "base_override": str|None}}
    """
    out = {}
    for line in section_text.splitlines():
        if not TABLE_ROW_RE.match(line):
            continue
        cells = split_row(line)
        if len(cells) < 3:
            continue
        token = cells[0]
        if not token.startswith("--"):
            continue
        hexes = re.findall(HEX_RE, " ".join(cells[1:3]))
        if len(hexes) != 2:
            continue  # 这一行没有一对完整的 light/dark 十六进制，不是数据行
        light, dark = hexes
        tail = " ".join(cells[3:])
        th_m = THRESHOLD_RE.search(tail)
        base_m = BASE_OVERRIDE_RE.search(tail)
        out[token] = {
            "light": light,
            "dark": dark,
            "threshold": float(th_m.group(1)) if th_m else None,
            "base_override": base_m.group(1) if base_m else None,
        }
    return out


def parse_presets(section_text):
    """解析 §3.3：`预设 | token | light | dark | 对比度 | 门限`，预设名只在组内首行给出。

    返回：{preset: {token: {...同上...}}}
    """
    presets = {}
    current_preset = None
    for line in section_text.splitlines():
        if not TABLE_ROW_RE.match(line):
            continue
        cells = split_row(line)
        if len(cells) < 4:
            continue
        preset_cell, token = cells[0], cells[1]
        if preset_cell:
            current_preset = preset_cell
        if not token.startswith("--") or current_preset is None:
            continue
        hexes = re.findall(HEX_RE, " ".join(cells[2:4]))
        if len(hexes) != 2:
            continue
        light, dark = hexes
        tail = " ".join(cells[4:])
        th_m = THRESHOLD_RE.search(tail)
        base_m = BASE_OVERRIDE_RE.search(tail)
        presets.setdefault(current_preset, {})[token] = {
            "light": light,
            "dark": dark,
            "threshold": float(th_m.group(1)) if th_m else None,
            "base_override": base_m.group(1) if base_m else None,
        }
    return presets


def resolve_base_hex(name, mode, neutral, semantic, preset_tokens):
    """按 token 名字取它在给定明暗态下的十六进制值——用于解析「于 --X」覆盖。"""
    for table in (preset_tokens, semantic, neutral):
        if name in table:
            return table[name][mode]
    return None


def main():
    contract_path = find_contract()
    text = contract_path.read_text(encoding="utf-8")

    neutral = parse_neutral_or_semantic(extract_section(text, "3.1"))
    semantic = parse_neutral_or_semantic(extract_section(text, "3.2"))
    presets = parse_presets(extract_section(text, "3.3"))

    if "--panel" not in neutral:
        die("§3.1 里没解析到 --panel——默认对比基准底缺失，无法继续。")
    if not presets:
        die("§3.3 一个预设都没解析到——正则或章节标题是不是被改了。")

    checks = []  # (label, ratio, threshold)
    skipped = []

    def add_checks(table, base_default_name, preset_tokens, preset_name, mode):
        base_default_hex = resolve_base_hex(base_default_name, mode, neutral, semantic, preset_tokens)
        for tok, row in table.items():
            th = row["threshold"]
            if th is None:
                skipped.append(f"{tok}（{preset_name}/{mode}）：契约未标门限，视为「不承诺」，不校验")
                continue
            base_name = row["base_override"] or base_default_name
            base_hex = resolve_base_hex(base_name, mode, neutral, semantic, preset_tokens)
            if base_hex is None:
                die(f"{tok} 要求基准 {base_name}，但在 {mode} 态下解析不到它的颜色值。")
            fg_hex = row[mode]
            ratio = contrast_ratio(fg_hex, base_hex)
            label = f"{tok} vs {base_name} · {preset_name}/{mode}"
            checks.append((label, ratio, th, fg_hex, base_hex))

    for preset_name, preset_tokens in presets.items():
        for mode in ("light", "dark"):
            # §3.1 中性色（--ink/--ink-2 默认于 --panel；--focus 行内覆盖于 --bg）
            add_checks(neutral, "--panel", preset_tokens, preset_name, mode)
            # §3.2 语义色（默认于 --panel；--fact-ink 行内覆盖于 --fact）
            add_checks(semantic, "--panel", preset_tokens, preset_name, mode)
            # §3.3 本预设的 accent 系（默认于 --panel；--accent-ink 行内覆盖于 --accent）
            add_checks(preset_tokens, "--panel", preset_tokens, preset_name, mode)

    if not checks:
        die("解析完契约表后一条可校验的组合都没有——解析器大概是坏的，不是契约没数据。")

    fail = 0
    print(f"── A1 对比度矩阵：{len(presets)} 预设 × 2 明暗，{len(checks)} 项组合（{len(skipped)} 项契约未承诺，不计入） ──")
    for label, ratio, threshold, fg, bg in checks:
        ok = ratio >= threshold - 1e-9
        mark = "✅" if ok else "❌"
        print(f"  {mark} {label:38s} 实算 {ratio:5.2f}:1  门限 ≥{threshold:g}:1  （{fg} on {bg}）")
        if not ok:
            fail += 1

    if skipped:
        print(f"\n  ⏭️  {len(skipped)} 项契约未标门限，不计入校验（举例）：")
        for s in skipped[:5]:
            print(f"     · {s}")

    print()
    if fail:
        print(f"❌ A1 不过：{fail}/{len(checks)} 项低于门限。上面 ❌ 标记的是哪一项、差多少，别只看这一行。", file=sys.stderr)
        return 1
    print(f"✅ A1 全绿：{len(checks)}/{len(checks)} 项组合达标（{contract_path}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
