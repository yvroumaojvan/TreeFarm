# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 7 轮：逻辑错误检测（unittest，零依赖）

目标：测 detect_logic_issues（_LogicVisitor + _ContractVisitor）的短板。
用例来源（权威）：BugsInPy 真实 bug 模式（tornado#1/#3/#6/#7）、
常见逻辑反模式（SonarSource rules）、Python 陷阱。

运行：
  python3 -m unittest tests.test_round7_logic -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_logic_issues  # noqa: E402


def make_file(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", prefix="treefarm_r7_")
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


class MutableDefaultTest(unittest.TestCase):
    def test_list_default(self):
        issues = issues_of('''\
def add_item(item, lst=[]):
    lst.append(item)
    return lst
''', "可变默认参数")
        self.assertTrue(len(issues) >= 1, f"[] 默认参数漏报: {issues}")

    def test_dict_default(self):
        issues = issues_of('''\
def cache(key, store={}):
    return store.setdefault(key, 0)
''', "可变默认参数")
        self.assertTrue(len(issues) >= 1, f"dict 默认参数漏报: {issues}")

    def test_safe_none_default(self):
        issues = issues_of('''\
def add_item(item, lst=None):
    if lst is None:
        lst = []
    lst.append(item)
    return lst
''', "可变默认参数")
        self.assertEqual([], issues, f"None 默认误报: {issues}")


class DivByZeroTest(unittest.TestCase):
    def test_div_by_var(self):
        issues = issues_of('''\
def avg(total, count):
    return total / count
''', "除零风险")
        self.assertTrue(len(issues) >= 1, f"除以变量漏报: {issues}")

    def test_safe_guarded(self):
        issues = issues_of('''\
def avg(total, count):
    if count == 0:
        return 0
    return total / count
''', "除零风险")
        self.assertEqual([], issues, f"保护分支误报: {issues}")

    def test_len_divisor(self):
        issues = issues_of('''\
def mean(nums):
    return sum(nums) / len(nums)
''', "除零风险")
        self.assertEqual([], issues, f"len 除数误报: {issues}")

    def test_str_percent_not_div(self):
        # "%s" % var 是格式化不是除法
        issues = issues_of('''\
def fmt(name):
    return "Hello %s" % name
''', "除零风险")
        self.assertEqual([], issues, f"字符串格式化误报: {issues}")


class BoundaryTest(unittest.TestCase):
    def test_off_by_one(self):
        issues = issues_of('''\
def neighbors(arr):
    out = []
    for i in range(len(arr)):
        out.append(arr[i + 1])
    return out
''', "边界条件")
        self.assertTrue(len(issues) >= 1, f"i+1 越界漏报: {issues}")

    def test_safe_len_minus_one(self):
        issues = issues_of('''\
def neighbors(arr):
    out = []
    for i in range(len(arr) - 1):
        out.append((arr[i], arr[i + 1]))
    return out
''', "边界条件")
        self.assertEqual([], issues, f"安全边界误报: {issues}")


class RaceConditionTest(unittest.TestCase):
    def test_thread_counter_no_lock(self):
        issues = issues_of('''\
import threading

counter = 0


def worker():
    global counter
    for _ in range(1000):
        counter += 1


threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
''', "竞态条件")
        self.assertTrue(len(issues) >= 1, f"全局自增无锁漏报: {issues}")

    def test_safe_with_lock(self):
        issues = issues_of('''\
import threading

counter = 0
lock = threading.Lock()


def worker():
    global counter
    for _ in range(1000):
        with lock:
            counter += 1


threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
''', "竞态条件")
        self.assertEqual([], issues, f"加锁误报: {issues}")

    def test_async_no_thread_no_race(self):
        # 纯 asyncio 无真线程 → 不报
        issues = issues_of('''\
import asyncio

total = 0


async def worker():
    global total
    total += 1


async def main():
    await asyncio.gather(*(worker() for _ in range(10)))
''', "竞态条件")
        self.assertEqual([], issues, f"asyncio 误报: {issues}")


class ToctouTest(unittest.TestCase):
    def test_exists_then_open(self):
        issues = issues_of('''\
import os


def read_file(path):
    if os.path.exists(path):
        return open(path).read()
    return None
''', "TOCTOU 竞争")
        self.assertTrue(len(issues) >= 1, f"exists+open 漏报: {issues}")


class CompareOperatorTest(unittest.TestCase):
    def test_is_literal(self):
        issues = issues_of('''\
def check(code):
    return code is 200
''', "比较运算符错误")
        self.assertTrue(len(issues) >= 1, f"is 200 漏报: {issues}")

    def test_safe_is_none(self):
        issues = issues_of('''\
def check(x):
    return x is None
''', "比较运算符错误")
        self.assertEqual([], issues, f"is None 误报: {issues}")


class GetThenDelTest(unittest.TestCase):
    def test_get_del_same_key(self):
        issues = issues_of('''\
def pop_header(headers, key):
    val = headers.get(key)
    del headers[key]
    return val
''', "字典键不存在访问")
        self.assertTrue(len(issues) >= 1, f"get 后 del 漏报: {issues}")

    def test_safe_guarded_get_del(self):
        issues = issues_of('''\
def pop_header(headers, key):
    if headers.get(key):
        val = headers[key]
        del headers[key]
        return val
    return None
''', "字典键不存在访问")
        self.assertEqual([], issues, f"保护分支误报: {issues}")


class SubmitReturnTest(unittest.TestCase):
    def test_executor_submit_return(self):
        issues = issues_of('''\
async def run_pool(pool, fn):
    return pool.submit(fn)
''', "协程未await")
        self.assertTrue(len(issues) >= 1, f"submit 直返漏报: {issues}")


class ContractDelegateTest(unittest.TestCase):
    def test_delegate_inconsistency(self):
        # tornado#1 型：多数方法用 self.ws_connection，唯独一个用 self.stream
        issues = issues_of('''\
class Connection:
    def __init__(self):
        self.stream = None
        self.ws_connection = None

    def write_message(self, msg):
        self.ws_connection.write(msg)

    def close(self):
        self.ws_connection.close()

    def ping(self, data):
        self.ws_connection.ping(data)

    def pong(self, data):
        self.ws_connection.pong(data)

    def send_error(self, code):
        self.stream.write(code)
''', "API契约")
        self.assertTrue(len(issues) >= 1, f"委托不一致漏报: {issues}")


class AbstractMethodTest(unittest.TestCase):
    def test_missing_abstract(self):
        issues = issues_of('''\
from abc import ABC, abstractmethod


class Base(ABC):
    @abstractmethod
    def run(self):
        pass

    @abstractmethod
    def stop(self):
        pass


class Impl(Base):
    def run(self):
        return "running"
''', "抽象方法未实现")
        self.assertTrue(len(issues) >= 1, f"抽象方法缺失漏报: {issues}")

    def test_safe_all_implemented(self):
        issues = issues_of('''\
from abc import ABC, abstractmethod


class Base(ABC):
    @abstractmethod
    def run(self):
        pass


class Impl(Base):
    def run(self):
        return "running"
''', "抽象方法未实现")
        self.assertEqual([], issues, f"已实现误报: {issues}")


if __name__ == "__main__":
    unittest.main()
