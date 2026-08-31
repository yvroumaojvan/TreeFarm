# -*- coding: utf-8 -*-
"""入口：演示入口函数豁免死代码检测。"""
from services.order import create_order
from utils.constants import APP_NAME, APP_VERSION


def main():
    print(f"{APP_NAME} v{APP_VERSION}")
    items = [("apple", 2), ("banana", 1)]
    order = create_order(items, "user-001")
    print(f"created: {order}")


if __name__ == "__main__":
    main()
