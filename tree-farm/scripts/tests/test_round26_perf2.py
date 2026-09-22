# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R7：性能检测 II（unittest）

运行：
  python3 -m unittest tests.test_round26_perf2 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_performance_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r26_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".py"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_performance_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class LoopConcatMoreTest(unittest.TestCase):
    def test_while_loop_concat(self):
        # while 循环内拼接
        issues = issues_of('''\
def build(n):
    s = ""
    i = 0
    while i < n:
        s += str(i)
        i += 1
    return s
''', "循环内字符串拼接")
        self.assertTrue(len(issues) >= 1, f"while 拼接漏报: {issues}")

    def test_join_in_list_comp_ok(self):
        # 列表推导里 join 是安全的
        issues = issues_of('''\
def build(items):
    return "".join(str(i) for i in items)
''', "循环内字符串拼接")
        self.assertEqual([], issues, f"join 误报: {issues}")

    def test_foreach_self_add(self):
        issues = issues_of('''\
def render(rows):
    html = ""
    for row in rows:
        html = html + "<tr>" + row + "</tr>"
    return html
''', "循环内字符串拼接")
        self.assertTrue(len(issues) >= 1, f"多次拼接漏报: {issues}")


class NPlusOneMoreTest(unittest.TestCase):
    def test_orm_loop_query(self):
        issues = issues_of('''\
def load_users(session, ids):
    users = []
    for i in ids:
        users.append(session.query(User).get(i))
    return users
''', "N+1 查询")
        self.assertTrue(len(issues) >= 1, f"ORM N+1 漏报: {issues}")

    def test_safe_batch_query(self):
        issues = issues_of('''\
def load_users(session, ids):
    return session.query(User).filter(User.id.in_(ids)).all()
''', "N+1 查询")
        self.assertEqual([], issues, f"批量查询误报: {issues}")


class LinearLookupMoreTest(unittest.TestCase):
    def test_counts_loop(self):
        issues = issues_of('''\
def count_matches(patterns, lines):
    out = []
    for p in patterns:
        out.append(lines.count(p))
    return out
''', "循环内线性查找")
        self.assertTrue(len(issues) >= 1, f".count() 循环漏报: {issues}")

    def test_any_in_loop(self):
        issues = issues_of('''\
def intersect(a, b):
    return [x for x in a if x in b]
''', "循环内线性查找")
        self.assertTrue(len(issues) >= 1, f"列表推导 in 漏报: {issues}")

    def test_tree_lookup_ok(self):
        # dict 键查找是 O(1)
        issues = issues_of('''\
def lookup(words, table):
    return [table.get(w) for w in words]
''', "循环内线性查找")
        self.assertEqual([], issues, f"dict 查找误报: {issues}")


class ResourceMoreTest(unittest.TestCase):
    def test_subprocess_resource(self):
        issues = issues_of('''\
import subprocess
def check(host):
    return subprocess.check_output(["ping", host])
''', "资源泄漏")
        # 无 close 需求 → 不报（subprocess 不是泄漏源）
        self.assertEqual([], issues, f"subprocess 误报: {issues}")

    def test_file_open_gc(self):
        issues = issues_of('''\
def read(path):
    f = open(path).read()
    return f
''', "资源泄漏")
        # open().read() 单行隐式关闭（CPython 引用计数）→ 弱信号可报可不报，不崩即可
        self.assertIsInstance(issues, list)


if __name__ == "__main__":
    unittest.main()