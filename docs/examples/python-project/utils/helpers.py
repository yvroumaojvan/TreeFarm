# -*- coding: utf-8 -*-
"""工具函数：演示死代码 + 代码异味。"""


def format_price(amount, currency="¥", digits=2, thousands=True, sign=False, symbol=True):
    """格式化价格（6 个参数，演示长参数列表异味）"""
    base = f"{amount:.{digits}f}"
    if thousands:
        base = _add_thousands(base)
    prefix = "+" if sign and amount > 0 else ""
    suffix = currency if symbol else ""
    return f"{prefix}{suffix}{base}"


def _add_thousands(num_str):
    """千分位：1234567 → 1,234,567"""
    int_part, _, dec = num_str.partition(".")
    groups = []
    while int_part:
        groups.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    return ",".join(groups) + (f".{dec}" if dec else "")


def dead_helper():
    """死代码：没有任何地方调用它"""
    return "nobody calls me"


def now_iso():
    """当前时间 ISO 格式"""
    from utils.constants import APP_NAME
    return f"{APP_NAME}-{__import__('time').time():.0f}"
