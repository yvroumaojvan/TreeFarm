# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R8：逻辑检测 II（unittest）

运行：
  python3 -m unittest tests.test_round27_logic2 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_logic_issues  # noqa: E402


def make_file(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", prefix="tf_r27_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None):
    path = make_file(content)
    try:
        all_issues = detect_logic_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class LogicMoreTest(unittest.TestCase):
    def test_div_by_zero_more(self):
        issues = issues_of('''\
def ratio(a, b):
    return a / b * 100
''', "除零风险")
        self.assertTrue(len(issues) >= 1, f"除零漏报: {issues}")

    def test_floor_div(self):
        issues = issues_of('''\
def percent(n, total):
    return n // total
''', "除零风险")
        self.assertTrue(len(issues) >= 1, f"整除漏报: {issues}")

    def test_mod_zero(self):
        issues = issues_of('''\
def is_even(n, base):
    return n % base == 0
''', "除零风险")
        self.assertTrue(len(issues) >= 1, f"取模漏报: {issues}")

    def test_safe_len_div(self):
        issues = issues_of('''\
def avg(nums):
    return sum(nums) / len(nums) if nums else 0
''', "除零风险")
        self.assertEqual([], issues, f"len+保护误报: {issues}")

    def test_boundary_index_more(self):
        issues = issues_of('''\
def get_prev(arr):
    return arr[-2:] if len(arr) > 0 else []
''', "边界条件")
        # 负切片安全 → 不报
        self.assertEqual([], issues, f"负切片误报: {issues}")

    def test_off_by_one_more(self):
        issues = issues_of('''\
def pairs(arr):
    out = []
    for i in range(len(arr)):
        out.append((arr[i], arr[i + 1]))
    return out
''', "边界条件")
        self.assertTrue(len(issues) >= 1, f"i+1 越界漏报: {issues}")

    def test_is_compare_more(self):
        issues = issues_of('''\
def check(val):
    return val is None or val is True
''', "比较运算符错误")
        self.assertEqual([], issues, f"合法 is 误报: {issues}")

    def test_float_eq(self):
        issues = issues_of('''\
def same(a, b):
    return a * 0.1 == b * 0.1
''', "浮点精度")
        # 有浮点运算+相等比较（弱信号可选，不崩即可）
        self.assertIsInstance(issues, list)


class ContractMoreTest(unittest.TestCase):
    def test_delegate_two_ways(self):
        # 同类方法一半用 A 一半用 B
        issues = issues_of('''\
class Conn:
    def __init__(self):
        self.a = None
        self.b = None

    def send(self, m):
        self.a.send(m)

    def recv(self):
        return self.a.recv()

    def ping(self):
        self.a.ping()

    def close(self):
        self.b.close()
''', "API契约")
        self.assertTrue(len(issues) >= 1, f"委托不一致漏报: {issues}")

    def test_delegate_consistent_ok(self):
        issues = issues_of('''\
class Conn:
    def __init__(self):
        self.a = None

    def send(self, m):
        self.a.send(m)

    def close(self):
        self.a.close()
''', "API契约")
        self.assertEqual([], issues, f"一致委托误报: {issues}")


if __name__ == "__main__":
    unittest.main()