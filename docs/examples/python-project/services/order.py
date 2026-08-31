# -*- coding: utf-8 -*-
"""订单服务：演示循环依赖 + 影响分析。"""
from services.inventory import check_stock, reserve


def create_order(items, user_id):
    """创建订单：检查库存 → 预留 → 返回订单号"""
    if not check_stock(items):
        raise ValueError("库存不足")
    return reserve(items, user_id)


def cancel_order(order_id):
    """取消订单：释放库存"""
    from services.inventory import release
    release(order_id)
    return f"order-{order_id}-cancelled"
