"""文本归一化的唯一一份：NFKC、去不可见字符（Unicode 类别 Cf 全部——零宽、软连字符、方向标记、U+2060、U+FEFF、U+180E 等——
加 U+034F 与变体选择符 U+FE00–U+FE0F / U+E0100–U+E01EF）、折叠所有空白（含制表符 / 换行）、strip、casefold。

泳道偏好的身份键与「忽略并记住」的匹配两边都用它——规则里存的和进来的窗口标题 / 程序名过同一个函数再比。
"""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE = re.compile("[\u034f\ufe00-\ufe0f\U000e0100-\U000e01ef]")


def fold(text: str | None) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    if not t.isascii():
        t = _INVISIBLE.sub("", "".join(c for c in t if unicodedata.category(c) != "Cf"))
    return unicodedata.normalize("NFKC", " ".join(t.split()).casefold())
