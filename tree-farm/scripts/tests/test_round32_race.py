# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R16：竞态/并发深化（unittest）

目标：验证竞态检测在更多真实形态（f-string 自增/字典嵌套/Event/线程池）的表现。

运行：
  python3 -m unittest tests.test_round32_race -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_logic_issues  # noqa: E402


def make_file(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".py", prefix="tf_r32_")
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


class RaceDeepTest(unittest.TestCase):
    """竞态深水区"""

    def test_dict_nested_counter(self):
        # 字典嵌套计数器无锁（真实 fake_device 形态）
        issues = issues_of('''\
import threading

_stats = {"msg": 0, "ring": 0}


def worker():
    for _ in range(100):
        _stats["msg"] += 1


threads = [threading.Thread(target=worker) for _ in range(5)]
for t in threads:
    t.start()
    t.join()
''', "竞态条件")
        self.assertTrue(len(issues) >= 1, f"字典嵌套竞态漏报: {issues}")

    def test_fstring_counter(self):
        issues = issues_of('''\
import threading

count = 0


def run():
    global count
    count += 1


ts = [threading.Thread(target=run) for _ in range(10)]
for t in ts:
    t.start()
''', "竞态条件")
        self.assertTrue(len(issues) >= 1, f"全局自增漏报: {issues}")


class SafeRaceTest(unittest.TestCase):
    """安全并发——不误报"""

    def test_join_all_threads(self):
        # join 全部线程 → 无竞态暴露面？保守：join 后仍看有无锁语义
        issues = issues_of('''\
import threading

count = 0
lock = threading.Lock()


def run():
    global count
    with lock:
        count += 1


ts = [threading.Thread(target=run) for _ in range(5)]
for t in ts:
    t.start()
for t in ts:
    t.join()
''', "竞态条件")
        self.assertEqual([], issues, f"加锁误报: {issues}")

    def test_no_threads_ok(self):
        issues = issues_of('''\
def count_to(n):
    total = 0
    for i in range(n):
        total += i
    return total
''', "竞态条件")
        self.assertEqual([], issues, f"无线程误报: {issues}")


if __name__ == "__main__":
    unittest.main()