# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R1：语义型 bug 检测（tornado#2/#4 攻坚）（unittest）

目标：验证 _scan_semantic_hints 三个弱信号启发式：
  - tornado#2：Transfer-Encoding 'not in' 忽略 chunked 值
  - tornado#4a：范围负偏移先比较后归一化
  - tornado#4b：范围对缺 start>=end 校验
要求：buggy 版命中、修复版豁免、普通代码零误报。

运行：
  python3 -m unittest tests.test_round20_semantic -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402


def logic_issues(code):
    d = tempfile.mkdtemp(prefix="tf_r20_")
    try:
        p = os.path.join(d, "app.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(code)
        return tf.detect_logic_issues([p])["issues"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def semantic_of(code):
    return [i for i in logic_issues(code) if i["type"] == "疑似语义缺陷"]


class Tornado2TETest(unittest.TestCase):
    """tornado#2：Transfer-Encoding chunked 判定"""

    def test_buggy_hit(self):
        # buggy 版：只有 not in，无 chunked 值比较 → 命中
        code = '''\
class HTTP1Connection:
    def _write_start_line(self, start_line, headers):
        self._chunking_output = (
            start_line.method in ("POST", "PUT", "PATCH")
            and "Content-Length" not in headers
            and "Transfer-Encoding" not in headers
        )
        return self._chunking_output
'''
        issues = semantic_of(code)
        self.assertTrue(len(issues) >= 1,
                        f"bug2 未命中: {[i['desc'][:40] for i in logic_issues(code)]}")
        self.assertIn("Transfer-Encoding", issues[0]["desc"])

    def test_fixed_exempt(self):
        # 修复版：补了 == "chunked" 值比较 → 豁免
        code = '''\
class HTTP1Connection:
    def _write_start_line(self, start_line, headers):
        self._chunking_output = (
            start_line.method in ("POST", "PUT", "PATCH")
            and "Content-Length" not in headers
            and (
                "Transfer-Encoding" not in headers
                or headers["Transfer-Encoding"] == "chunked"
            )
        )
        return self._chunking_output
'''
        self.assertEqual([], semantic_of(code), f"修复版误报: {semantic_of(code)}")

    def test_no_chunked_context_exempt(self):
        # 无 chunked 语境（普通头检查）→ 不命中
        code = '''\
def check(headers):
    return "Transfer-Encoding" not in headers and "Content-Length" not in headers
'''
        self.assertEqual([], semantic_of(code), f"普通头检查误报: {semantic_of(code)}")


class Tornado4aTest(unittest.TestCase):
    """tornado#4a：负偏移先比较后归一化"""

    def test_buggy_hit(self):
        code = '''\
def get_content_range(request_range, size):
    start, end = request_range
    if (start is not None and start >= size) or end == 0:
        return 416
    if start is not None and start < 0:
        start += size
    if end is not None and end > size:
        end = size
    return (start, end)
'''
        issues = semantic_of(code)
        self.assertTrue(len(issues) >= 1, f"bug4a 未命中: {semantic_of(code)}")

    def test_fixed_exempt(self):
        # 修复版：负偏移归一化提前到 416 检查之前 → 豁免
        code = '''\
def get_content_range(request_range, size):
    start, end = request_range
    if start is not None and start < 0:
        start += size
        if start < 0:
            start = 0
    if (start is not None
            and (start >= size or (end is not None and start >= end))
       ) or end == 0:
        return 416
    if end is not None and end > size:
        end = size
    return (start, end)
'''
        self.assertEqual([], semantic_of(code), f"修复版误报: {semantic_of(code)}")


class Tornado4bTest(unittest.TestCase):
    """tornado#4b：缺 start>=end 校验"""

    def test_buggy_hit(self):
        code = '''\
def get_content_range(request_range, size):
    start, end = request_range
    if (start is not None and start >= size) or end == 0:
        return 416
    if start is not None and start < 0:
        start += size
    if end is not None and end > size:
        end = size
    return (start, end)
'''
        issues = semantic_of(code)
        self.assertTrue(len(issues) >= 1, f"bug4b 未命中: {semantic_of(code)}")

    def test_fixed_exempt(self):
        # 修复版：416 检查含 start >= end → 豁免
        code = '''\
def get_content_range(request_range, size):
    start, end = request_range
    if start is not None and start < 0:
        start += size
    if (start is not None
            and (start >= size or (end is not None and start >= end))
       ) or end == 0:
        return 416
    if end is not None and end > size:
        end = size
    return (start, end)
'''
        self.assertEqual([], semantic_of(code), f"修复版误报: {semantic_of(code)}")


class FalsePositiveGuardTest(unittest.TestCase):
    """普通代码零误报"""

    def test_normal_index_code(self):
        code = '''\
def clamp(x, lo, hi):
    if x < lo:
        x = lo
    if x > hi:
        x = hi
    return x
'''
        self.assertEqual([], semantic_of(code), f"普通代码误报: {semantic_of(code)}")

    def test_normal_headers_code(self):
        code = '''\
def handle(headers):
    if "Content-Type" not in headers:
        return "text/plain"
    return headers["Content-Type"]
'''
        self.assertEqual([], semantic_of(code), f"普通头代码误报: {semantic_of(code)}")


if __name__ == "__main__":
    unittest.main()
