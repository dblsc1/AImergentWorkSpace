"""check-contrast.py（A1）的 pytest 级单元测试。

新增代码必须同批新增测试。check-contrast.py 是一段纯计算 + 纯文本
解析的脚本，不依赖网络或容器，天然适合单测——不必像顶栏那样非拉真浏览器不可。

跑法：
    ../gantt/review/.venv/bin/python -m pytest code/backend/tests/test_check_contrast.py -q
或任何装了 pytest 的解释器（本文件不依赖 playwright）。
"""
import importlib.util
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.join(_HERE, "..", "scripts", "check-contrast.py")


@pytest.fixture(scope="module")
def cc():
    spec = importlib.util.spec_from_file_location("check_contrast", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hex_to_rgb(cc):
    assert cc.hex_to_rgb("#FFFFFF") == (255, 255, 255)
    assert cc.hex_to_rgb("#000000") == (0, 0, 0)
    assert cc.hex_to_rgb("#0B7A6F") == (0x0B, 0x7A, 0x6F)


def test_contrast_ratio_black_on_white_is_21_to_1(cc):
    # WCAG 教科书值：纯黑纯白的对比度恰好是 21:1（相对亮度公式的边界情形）。
    ratio = cc.contrast_ratio("#000000", "#FFFFFF")
    assert abs(ratio - 21.0) < 0.01


def test_contrast_ratio_same_color_is_1_to_1(cc):
    assert abs(cc.contrast_ratio("#6D5AE6", "#6D5AE6") - 1.0) < 1e-9


def test_contrast_ratio_symmetric(cc):
    a = cc.contrast_ratio("#0B7A6F", "#FFFFFF")
    b = cc.contrast_ratio("#FFFFFF", "#0B7A6F")
    assert abs(a - b) < 1e-9


def test_contract_declared_fact_light_matches_our_calc(cc):
    # 契约表 §3.2 写的是「--fact light 于 --panel light：5.21」。
    # 用契约自己给的两个十六进制值重新算一遍，应该落在同一个数字附近
    # （契约的小数位是四舍五入过的，允许 0.01 量级的误差）。
    ratio = cc.contrast_ratio("#0B7A6F", "#FFFFFF")
    assert abs(ratio - 5.21) < 0.02


def test_split_row_strips_backticks_and_pipes(cc):
    cells = cc.split_row("| `--fact` | `#0B7A6F` | `#2FD4C2` | 5.21 / 9.15 | ≥4.5 |")
    assert cells == ["--fact", "#0B7A6F", "#2FD4C2", "5.21 / 9.15", "≥4.5"]


def test_parse_neutral_or_semantic_reads_threshold_and_base_override(cc):
    text = (
        "| token | light | dark | 对比度（亮/暗 于 --panel） | 门限 |\n"
        "|---|---|---|---|---|\n"
        "| `--fact` | `#0B7A6F` | `#2FD4C2` | 5.21 / 9.15 | ≥4.5 |\n"
        "| `--fact-ink` | `#FFFFFF` | `#04211D` | 5.21 / 9.12 于 --fact | ≥4.5 |\n"
        "| `--fact-soft` | `#DCF0ED` | `#123B38` | 条底，不承诺 | —— |\n"
    )
    rows = cc.parse_neutral_or_semantic(text)
    assert rows["--fact"]["threshold"] == 4.5
    assert rows["--fact"]["base_override"] is None
    assert rows["--fact-ink"]["base_override"] == "--fact"
    assert rows["--fact-soft"]["threshold"] is None  # 「不承诺」的行不该解出门限


def test_parse_presets_carries_preset_name_across_blank_rows(cc):
    text = (
        "| 预设 | token | light | dark | 对比度 | 门限 |\n"
        "|---|---|---|---|---|---|\n"
        "| `teal`（默认） | `--accent` | `#0B7A6F` | `#2FD4C2` | 5.21 / 9.15 | ≥4.5 |\n"
        "|  | `--accent-ink` | `#FFFFFF` | `#04211D` | 5.21 / 9.12 于 --accent | ≥4.5 |\n"
        "| `violet` | `--accent` | `#6D5AE6` | `#9B8CFF` | 4.93 / 6.13 | ≥4.5 |\n"
    )
    presets = cc.parse_presets(text)
    assert set(presets.keys()) == {"teal（默认）", "violet"}
    assert presets["teal（默认）"]["--accent-ink"]["base_override"] == "--accent"
    assert presets["violet"]["--accent"]["light"] == "#6D5AE6"


def test_full_pipeline_against_real_contract_is_all_green(cc):
    contract_path = cc.find_contract()
    text = contract_path.read_text(encoding="utf-8")
    neutral = cc.parse_neutral_or_semantic(cc.extract_section(text, "3.1"))
    semantic = cc.parse_neutral_or_semantic(cc.extract_section(text, "3.2"))
    presets = cc.parse_presets(cc.extract_section(text, "3.3"))
    assert "--panel" in neutral
    assert len(presets) == 3  # teal / violet / amber

    fail = 0
    checked = 0
    for preset_name, preset_tokens in presets.items():
        for mode in ("light", "dark"):
            for table, base_default in ((neutral, "--panel"), (semantic, "--panel"),
                                         (preset_tokens, "--panel")):
                for tok, row in table.items():
                    th = row["threshold"]
                    if th is None:
                        continue
                    base_name = row["base_override"] or base_default
                    base_hex = cc.resolve_base_hex(base_name, mode, neutral, semantic, preset_tokens)
                    ratio = cc.contrast_ratio(row[mode], base_hex)
                    checked += 1
                    if ratio < th - 1e-9:
                        fail += 1
    assert checked >= 72  # 3 预设 × 2 明暗 × 12 个有门限的 token
    assert fail == 0


def test_pipeline_catches_a_deliberately_broken_color(cc):
    """可失败性：把 --danger 亮色改成一个明显不达标的浅色，管线必须判红。

    不写回任何仓内文件——只在内存里替换文本，重新走一遍真实的 parse_*/contrast_ratio。
    """
    contract_path = cc.find_contract()
    text = contract_path.read_text(encoding="utf-8")
    mutated = text.replace(
        "| `--danger` | `#C2333B` | `#FF8189`",
        "| `--danger` | `#FFB3B8` | `#FF8189`",
    )
    assert mutated != text, "替换没生效，字符串大概与契约当前原文不一致了"

    neutral = cc.parse_neutral_or_semantic(cc.extract_section(mutated, "3.1"))
    semantic = cc.parse_neutral_or_semantic(cc.extract_section(mutated, "3.2"))
    presets = cc.parse_presets(cc.extract_section(mutated, "3.3"))

    row = semantic["--danger"]
    base_hex = cc.resolve_base_hex("--panel", "light", neutral, semantic, next(iter(presets.values())))
    ratio = cc.contrast_ratio(row["light"], base_hex)
    assert ratio < row["threshold"], "构造的坏值没有低于门限，自测场景没搭对"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
