# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 6 轮：性能问题检测（unittest，零依赖）

目标：测 detect_performance_issues（Python AST + 文本级复杂度嗅探）的短板。
用例来源（权威）：3DGS 实战 O(n²) 漏报复盘、常见性能反模式（OWASP 性能安全
实践、SonarSource performance rules）、真实业务场景。

运行：
  python3 -m unittest tests.test_round6_perf -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_performance_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_r6_")
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


class LoopStringConcatTest(unittest.TestCase):
    """循环内字符串拼接（O(n²)）"""

    def test_aug_assign_concat(self):
        issues = issues_of('''\
def build(items):
    s = ""
    for it in items:
        s += str(it)
    return s
''', "循环内字符串拼接")
        self.assertTrue(len(issues) >= 1, f"+= 拼接漏报: {issues}")

    def test_assign_concat(self):
        issues = issues_of('''\
def build(items):
    s = ""
    for it in items:
        s = s + it
    return s
''', "循环内字符串拼接")
        self.assertTrue(len(issues) >= 1, f"s = s + x 漏报: {issues}")

    def test_safe_join(self):
        # 安全：join → 不报
        issues = issues_of('''\
def build(items):
    return "".join(str(i) for i in items)
''', "循环内字符串拼接")
        self.assertEqual([], issues, f"join 误报: {issues}")

    def test_int_increment_no_fp(self):
        # 整数累加不是字符串拼接 → 不报
        issues = issues_of('''\
def count(n):
    total = 0
    for i in range(n):
        total += i
    return total
''', "循环内字符串拼接")
        self.assertEqual([], issues, f"整数累加误报: {issues}")


class LinearLookupTest(unittest.TestCase):
    """循环内线性查找（O(n²)）"""

    def test_in_list(self):
        issues = issues_of('''\
def dedupe(items, cache):
    out = []
    for x in items:
        if x in cache:
            continue
        out.append(x)
    return out
''', "循环内线性查找")
        self.assertTrue(len(issues) >= 1, f"in list 漏报: {issues}")

    def test_index_in_loop(self):
        issues = issues_of('''\
def find_positions(items, targets):
    out = []
    for t in targets:
        out.append(items.index(t))
    return out
''', "循环内线性查找")
        self.assertTrue(len(issues) >= 1, f".index() 漏报: {issues}")

    def test_safe_set_membership(self):
        # 安全：set 判断 → 不报
        issues = issues_of('''\
def dedupe(items, seen):
    out = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out
''', "循环内线性查找")
        self.assertEqual([], issues, f"set 误报: {issues}")


class RecompileNPlusOneTest(unittest.TestCase):
    """循环内 re.compile / N+1 查询"""

    def test_recompile_in_loop(self):
        issues = issues_of('''\
import re


def match_all(lines):
    out = []
    for ln in lines:
        m = re.compile(r"^\\d+").match(ln)
        if m:
            out.append(m.group())
    return out
''', "循环内正则编译")
        self.assertTrue(len(issues) >= 1, f"循环内 re.compile 漏报: {issues}")

    def test_n_plus_one(self):
        issues = issues_of('''\
def load_users(conn, user_ids):
    users = []
    for uid in user_ids:
        users.append(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())
    return users
''', "N+1 查询")
        self.assertTrue(len(issues) >= 1, f"N+1 漏报: {issues}")

    def test_safe_compile_outside(self):
        issues = issues_of('''\
import re
pat = re.compile(r"^\\d+")


def match_all(lines):
    return [m.group() for ln in lines if (m := pat.match(ln))]
''', "循环内正则编译")
        self.assertEqual([], issues, f"循环外编译误报: {issues}")


class RecursionTest(unittest.TestCase):
    """递归无终止/无缓存"""

    def test_recursion_no_base(self):
        issues = issues_of('''\
def recurse(n):
    return recurse(n - 1) + recurse(n - 2)
''', "递归无终止")
        self.assertTrue(len(issues) >= 1, f"递归无终止漏报: {issues}")

    def test_recursion_no_cache(self):
        issues = issues_of('''\
def fib(n):
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)
''', "递归无缓存")
        self.assertTrue(len(issues) >= 1, f"递归无缓存漏报: {issues}")

    def test_safe_lru_cache(self):
        issues = issues_of('''\
from functools import lru_cache


@lru_cache(maxsize=None)
def fib(n):
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)
''', "递归无缓存")
        self.assertEqual([], issues, f"lru_cache 误报: {issues}")


class ResourceLeakTest(unittest.TestCase):
    """资源泄漏"""

    def test_open_no_with_no_close(self):
        issues = issues_of('''\
def read_all(path):
    f = open(path)
    return f.read()
''', "资源泄漏")
        self.assertTrue(len(issues) >= 1, f"open 无 close 漏报: {issues}")

    def test_socket_no_close(self):
        issues = issues_of('''\
import socket


def connect(host):
    s = socket.socket()
    s.connect((host, 80))
    return s.sendall(b"hi")
''', "资源泄漏")
        self.assertTrue(len(issues) >= 1, f"socket 无 close 漏报: {issues}")

    def test_safe_with(self):
        issues = issues_of('''\
def read_all(path):
    with open(path) as f:
        return f.read()
''', "资源泄漏")
        self.assertEqual([], issues, f"with 误报: {issues}")

    def test_safe_close(self):
        issues = issues_of('''\
def read_all(path):
    f = open(path)
    try:
        return f.read()
    finally:
        f.close()
''', "资源泄漏")
        self.assertEqual([], issues, f"finally close 误报: {issues}")


class NestedLoopOn2Test(unittest.TestCase):
    """嵌套循环 + 集合访问 = 疑似 O(n²)"""

    def test_py_nested_index(self):
        issues = issues_of('''\
def check_pairs(a, b):
    out = []
    for i in range(len(a)):
        for j in range(len(b)):
            if a[i] == b[j]:
                out.append((i, j))
    return out
''', "疑似O(n²)嵌套循环+集合访问")
        self.assertTrue(len(issues) >= 1, f"Python 嵌套 O(n²) 漏报: {issues}")

    def test_java_nested_get(self):
        issues = issues_of('''\
class Grid {
    double[] data;
    int size;

    double[] scan(int radius) {
        double[] out = new double[size];
        for (int i = 0; i < size; i++) {
            for (int j = 0; j < size; j++) {
                out[i] += grid.get(i * size + j);
            }
        }
        return out;
    }
}
''', "疑似O(n²)嵌套循环+集合访问", ".java")
        self.assertTrue(len(issues) >= 1, f"Java 嵌套 O(n²) 漏报: {issues}")

    def test_js_nested_concat(self):
        issues = issues_of('''\
function buildMatrix(rows) {
    let s = "";
    for (let i = 0; i < rows.length; i++) {
        for (let j = 0; j < rows[i].length; j++) {
            s += rows[i][j];
        }
    }
    return s;
}
''', None, ".js")
        # rows[i][j] 下标访问优先命中「集合访问」类型，或报「嵌套拼接」——任一即算命中
        self.assertTrue(len(issues) >= 1,
                        f"JS 嵌套循环漏报（应报 O(n²)/拼接其一）: {issues}")


if __name__ == "__main__":
    unittest.main()
