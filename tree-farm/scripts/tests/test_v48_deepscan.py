# -*- coding: utf-8 -*-
"""
树场 v4.8 测试套件（unittest，零依赖）

运行：
  python3 -m unittest discover -s tests -v
  或直接 python3 tests/test_v48_deepscan.py

覆盖：
- deep_scan 全自动深度体检：大树识别 / 每树思维链 / 跨树串联 / 汇总
- v4.8 新静态规则：get 后 del 同一字典 key（tornado#3 型）、executor.submit 直返（tornado#7 型）
"""
import os
import shutil
import tempfile
import unittest

from treefarm.analysis import detect_logic_issues
from treefarm.core import TreeFarm


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _run_logic(tmp):
    """对临时目录跑逻辑检测，返回 issue 类型集合"""
    result = detect_logic_issues([os.path.join(tmp, "app.py")], root=tmp)
    return {i["type"] for i in result["issues"]}


class TestDeepScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 两个文件互相引用：a.py 被 b.py 引用 → a 是大树；独立 c.py 无引用
        _write(os.path.join(self.tmp, "a.py"),
               "def helper():\n    return 1\n")
        _write(os.path.join(self.tmp, "b.py"),
               "import a\n"
               "def main():\n"
               "    return a.helper()\n")
        _write(os.path.join(self.tmp, "c.py"),
               "x = 1\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deep_scan_runs(self):
        farm = TreeFarm(self.tmp)
        farm.plant()
        out = farm.deep_scan()
        self.assertIn("全自动深度体检", out)
        self.assertIn("大树", out)
        self.assertIn("汇总", out)

    def test_deep_scan_identifies_core_tree(self):
        farm = TreeFarm(self.tmp)
        farm.plant()
        out = farm.deep_scan()
        # a.py 被 b.py import → 应是大树；b.py 引用 a 也是大树（有基因）
        self.assertIn("a.py", out)
        self.assertIn("b.py", out)

    def test_deep_scan_marks_weed(self):
        farm = TreeFarm(self.tmp)
        farm.plant()
        out = farm.deep_scan()
        self.assertIn("杂草", out)

    def test_deep_scan_summary(self):
        farm = TreeFarm(self.tmp)
        farm.plant()
        out = farm.deep_scan()
        self.assertIn("── 汇总", out)


class TestV48StaticRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_then_del_dict_key(self):
        """tornado#3 型：get 后 del 同一字典 key → KeyError 隐患"""
        _write(os.path.join(self.tmp, "app.py"),
               "cache = {}\n"
               "def close(io_loop):\n"
               "    if cache.get(io_loop) is not None:\n"
               "        raise RuntimeError('x')\n"
               "    del cache[io_loop]\n")
        types = _run_logic(self.tmp)
        self.assertIn("字典键不存在访问", types)

    def test_get_then_del_no_false_positive(self):
        """正常 del（无前置 get）不误报"""
        _write(os.path.join(self.tmp, "app.py"),
               "def clean(d, key):\n"
               "    if key in d:\n"
               "        del d[key]\n")
        types = _run_logic(self.tmp)
        self.assertNotIn("字典键不存在访问", types)

    def test_submit_returned_directly(self):
        """tornado#7 型：executor.submit() 返回值直返 → 不可 await"""
        _write(os.path.join(self.tmp, "app.py"),
               "class Loop:\n"
               "    def run_in_executor(self, executor, func):\n"
               "        return executor.submit(func)\n")
        types = _run_logic(self.tmp)
        self.assertIn("协程未await", types)

    def test_submit_wrapped_no_false_positive(self):
        """正确做法（包装成可 await Future）不误报"""
        _write(os.path.join(self.tmp, "app.py"),
               "import asyncio\n"
               "def run(executor, func):\n"
               "    f = executor.submit(func)\n"
               "    return asyncio.wrap_future(f)\n")
        types = _run_logic(self.tmp)
        self.assertNotIn("协程未await", types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
