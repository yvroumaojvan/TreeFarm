# -*- coding: utf-8 -*-
"""库存服务：与 order 形成循环依赖（order→inventory→order）。"""
from services.order import cancel_order

STOCK = {"apple": 100, "banana": 50}


def check_stock(items):
    """检查库存是否充足"""
    for name, qty in items:
        if STOCK.get(name, 0) < qty:
            return False
    return True


def reserve(items, user_id):
    """预留库存"""
    for name, qty in items:
        STOCK[name] = STOCK.get(name, 0) - qty
    return f"order-{user_id}"


def release(order_id):
    """释放库存（被 order.cancel_order 调用）"""
    return cancel_order(order_id)
