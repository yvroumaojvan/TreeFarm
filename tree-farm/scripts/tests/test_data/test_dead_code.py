# -*- coding: utf-8 -*-
"""
测试用的 Python 模块
用于测试死代码检测、复杂度分析等功能
"""


def used_function():
    """被调用的函数"""
    return "used"


def dead_function():
    """死代码：从未被调用的函数"""
    return "dead"


class UsedClass:
    """被使用的类"""
    
    def method1(self):
        return 1
    
    def method2(self):
        return 2


class DeadClass:
    """死代码：从未被使用的类"""
    
    def method(self):
        return "dead"


def complex_function(x, y, z):
    """复杂度较高的函数"""
    result = 0
    
    if x > 0:
        if y > 0:
            result = x + y
        else:
            result = x - y
    elif x < 0:
        if z > 0:
            result = -x + z
        else:
            result = -x - z
    else:
        result = y + z
    
    for i in range(10):
        result += i
        if i % 2 == 0:
            result *= 2
    
    while result < 100:
        result += 10
    
    try:
        result = result / 2
    except Exception:
        result = 0
    
    return result


def main():
    """入口函数"""
    obj = UsedClass()
    print(used_function())
    print(obj.method1())
    print(complex_function(1, 2, 3))


if __name__ == "__main__":
    main()
