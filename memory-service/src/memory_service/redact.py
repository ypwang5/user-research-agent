"""确定性脱敏：入库前把可识别个人的信息替换成占位符。

人名这类正则抓不全的，由调用方通过 names 显式传入（Dify 里可让 LLM 先识别）。
顺序有讲究：先长后短，身份证/银行卡要在手机号之前处理，避免被截成手机号。
"""

import re

_RULES: list[tuple[str, re.Pattern]] = [
    ("[邮箱]", re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")),
    ("[身份证]", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("[银行卡]", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("[手机号]", re.compile(r"(?<!\d)(\+?86[- ]?)?1[3-9]\d[- ]?\d{4}[- ]?\d{4}(?!\d)")),
    ("[微信]", re.compile(r"(微信号?|vx|VX|wx|WX|v信)\s*[:：]?\s*[A-Za-z][\w-]{5,19}")),
    ("[QQ]", re.compile(r"(QQ|qq)号?\s*[:：]?\s*\d{5,11}")),
]


def redact(text: str, names: list[str] | None = None) -> tuple[str, dict[str, int]]:
    """返回 (脱敏后文本, 各类命中次数)。"""
    counts: dict[str, int] = {}
    for i, name in enumerate(sorted(set(names or []), key=len, reverse=True)):
        if not name.strip():
            continue
        token = f"[受访者{i + 1}]" if len(names or []) > 1 else "[受访者]"
        n = text.count(name)
        if n:
            text = text.replace(name, token)
            counts["人名"] = counts.get("人名", 0) + n
    for label, pat in _RULES:
        text, n = pat.subn(label, text)
        if n:
            counts[label] = counts.get(label, 0) + n
    return text, counts
