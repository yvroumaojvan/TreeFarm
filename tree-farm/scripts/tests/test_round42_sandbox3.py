# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R38：沙箱逃逸 III（unittest）

运行：
  python3 -m unittest tests.test_round42_sandbox3 -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.sandbox import SandboxRunner, SandboxConfig  # noqa: E402


def make_runner(security_level="restricted", **kw):
    cfg = SandboxConfig(enabled=True, security_level=security_level, max_cpu_seconds=5)
    for k, v in kw.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return SandboxRunner(cfg)


class EscapeVariantTest(unittest.TestCase):
    def setUp(self):
        self.r = make_runner(security_level="restricted")

    def test_format_attack(self):
        # format 字符串访问类（沙箱逃逸经典）
        r = self.r.run_code("print('{0.__class__}'.format(1))")
        self.assertFalse(r.success)

    def test_mro_chain(self):
        r = self.r.run_code("print((1).__class__.__mro__)")
        self.assertFalse(r.success)

    def test_bytecode_attack(self):
        # compile 沙箱绕过尝试
        r = self.r.run_code("compile('print(1)', '<s>', 'exec')")
        self.assertFalse(r.success)

    def test_cell_var_attack(self):
        r = self.r.run_code("print((lambda: 0).__closure__)")
        self.assertFalse(r.success)

    def test_os_module_attr(self):
        # 通过模块属性链访问 os
        r = self.r.run_code("import importlib\nimportlib.import_module('os')")
        self.assertFalse(r.success)

    def test_sys_modules_escape(self):
        r = self.r.run_code("print(sys.modules['os'])")
        self.assertFalse(r.success)

    def test_normal_compute_ok(self):
        r = self.r.run_code("print([i*i for i in range(5)])")
        self.assertTrue(r.success)


class SubprocessVariantTest(unittest.TestCase):
    def test_binary_write_blocked(self):
        r = make_runner(security_level="subprocess").run_code("open('/sdcard/x','w')")
        self.assertFalse(r.success)

    def test_input_bomb(self):
        # 大 input 不卡死
        r = make_runner(security_level="restricted").run_code("x = input()", stdin="A" * 100000)
        self.assertIsInstance(r, object)


if __name__ == "__main__":
    unittest.main()