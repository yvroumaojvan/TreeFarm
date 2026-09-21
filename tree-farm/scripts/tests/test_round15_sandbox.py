# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 15 轮：沙箱逃逸拦截矩阵（unittest，零依赖）

目标：验证沙箱（restricted + subprocess 双路径）对常见逃逸手法全部拦截。
用例来源：Python 沙箱逃逸经典手法（__globals__/__subclasses__/getattr/
import 绕过/网络/文件 IO）、资源限制（超时/递归/输出炸弹）。

运行：
  python3 -m unittest tests.test_round15_sandbox -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.sandbox import SandboxRunner, SandboxConfig  # noqa: E402


def make_runner(enabled=True, **kwargs):
    cfg = SandboxConfig(enabled=enabled)
    for k, v in kwargs.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return SandboxRunner(cfg)


class RestrictedEscapeInterceptTest(unittest.TestCase):
    """restricted 路径逃逸拦截"""

    def setUp(self):
        self.runner = make_runner(security_level="restricted", max_cpu_seconds=5)

    def test_open_blocked(self):
        r = self.runner.run_code('open("/etc/passwd")')
        self.assertFalse(r.success)
        self.assertIn("禁止", r.error)

    def test_import_os_blocked(self):
        r = self.runner.run_code("import os\nos.system('id')")
        self.assertFalse(r.success)
        self.assertIn("禁止导入", r.error)

    def test_globals_escape_blocked(self):
        r = self.runner.run_code("__builtins__['open']")
        self.assertFalse(r.success)

    def test_class_chain_blocked(self):
        r = self.runner.run_code("().__class__.__bases__")
        self.assertFalse(r.success)
        self.assertIn("禁止访问", r.error)

    def test_getattr_blocked(self):
        r = self.runner.run_code("getattr(__builtins__, 'eval')")
        self.assertFalse(r.success)

    def test_exec_blocked(self):
        r = self.runner.run_code("exec('print(1)')")
        self.assertFalse(r.success)

    def test_normal_code_ok(self):
        r = self.runner.run_code("x = [1, 2, 3]\nprint(sum(x))")
        self.assertTrue(r.success)
        self.assertIn("6", r.stdout)


class SubprocessEscapeInterceptTest(unittest.TestCase):
    """subprocess 路径逃逸拦截（v4.5 修复后补的代码级防护）"""

    def setUp(self):
        self.runner = make_runner(security_level="subprocess", max_cpu_seconds=5)

    def test_os_system_blocked(self):
        r = self.runner.run_code("import os\nos.system('id')")
        self.assertFalse(r.success)
        self.assertIn("沙箱安全拦截", r.error)

    def test_socket_blocked(self):
        r = self.runner.run_code("import socket\ns = socket.socket()")
        self.assertFalse(r.success)
        self.assertIn("禁止导入网络模块", r.error)

    def test_requests_blocked(self):
        r = self.runner.run_code("import requests\nrequests.get('http://x')")
        self.assertFalse(r.success)

    def test_subprocess_blocked(self):
        r = self.runner.run_code("import subprocess\nsubprocess.run(['ls'])")
        self.assertFalse(r.success)
        self.assertIn("禁止调用子进程", r.error)

    def test_dunder_attr_blocked(self):
        r = self.runner.run_code("x = 1\nprint(x.__class__)")
        self.assertFalse(r.success)

    def test_pickle_blocked(self):
        r = self.runner.run_code("import pickle\npickle.loads(b'x')")
        self.assertFalse(r.success)
        self.assertIn("禁止导入危险模块", r.error)


class ResourceLimitTest(unittest.TestCase):
    """资源限制：超时/输出炸弹"""

    def test_infinite_loop_timeout(self):
        runner = make_runner(security_level="subprocess", max_cpu_seconds=2)
        r = runner.run_code("while True:\n    pass")
        self.assertFalse(r.success)
        self.assertTrue(r.error_category in ("timeout", "unknown"),
                        f"错误分类异常: {r.error_category}")

    def test_output_bomb_limited(self):
        runner = make_runner(security_level="restricted", max_output_kb=2)
        r = runner.run_code("print('A' * 100000)")
        # restricted 路径输出限制主要靠捕获后截断，不崩即可
        self.assertTrue(r.success or "输出" in (r.error or ""))

    def test_error_classification(self):
        runner = make_runner(security_level="restricted")
        r = runner.run_code("x = 1 / 0")
        r.classify_error()
        self.assertEqual(r.error_category, "runtime")


class SandboxDisabledTest(unittest.TestCase):
    def test_disabled_returns_friendly_error(self):
        runner = make_runner(enabled=False)
        r = runner.run_code("print('hi')")
        self.assertFalse(r.success)
        self.assertIn("未开启", r.error)


if __name__ == "__main__":
    unittest.main()
