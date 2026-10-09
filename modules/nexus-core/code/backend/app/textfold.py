"""文本归一化的唯一一份：NFKC、去零宽字符、折叠所有空白（含制表符 / 换行）、strip、casefold。

泳道偏好的身份键与「忽略并记住」的匹配两边都用它——规则里存的和进来的窗口标题 / 程序名过同一个函数再比。
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = re.compile("[​-‍⁠﻿]")


def fold(text: str | None) -> str:
    t = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text or ""))
    return unicodedata.normalize("NFKC", " ".join(t.split()).casefold())
