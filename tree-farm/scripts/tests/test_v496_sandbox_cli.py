"""树场 v4.9.6 沙箱 CLI 缺参友好提示测试套件（unittest，零依赖）。

背景：扣子AI 复测报告指出 `--sandbox run/test/scan` 帮助声明支持但实际报
「未知沙箱命令」——实锤根因是 run/trace/cov/test/scan/quality/profile
分支要求 `len(rest) >= 2`（必须有后续参数），缺参数时掉进「未知命令」兜底
分支，误导用户以为功能不存在。v4.9.6 修复：已知命令缺参数 → 明确提示用法；
只有命令完全不在列表里才报「未知沙箱命令」。
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from treefarm.cli import _dispatch_sandbox


class TestSandboxCLIMissingArg(unittest.TestCase):
    """缺参数时给友好提示，不误报「未知沙箱命令」。"""

    def test_run_without_code_hints_missing_arg(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dispatch_sandbox(["run"])
        out = buf.getvalue()
        self.assertIn("缺少参数", out)
        self.assertIn("run <code>", out)
        self.assertNotIn("未知沙箱命令", out)

    def test_test_without_file_hints_missing_arg(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dispatch_sandbox(["test"])
        out = buf.getvalue()
        self.assertIn("缺少参数", out)
        self.assertNotIn("未知沙箱命令", out)

    def test_scan_without_file_hints_missing_arg(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dispatch_sandbox(["scan"])
        out = buf.getvalue()
        self.assertIn("缺少参数", out)
        self.assertNotIn("未知沙箱命令", out)

    def test_trace_without_code_hints_missing_arg(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dispatch_sandbox(["trace"])
        out = buf.getvalue()
        self.assertIn("缺少参数", out)
        self.assertNotIn("未知沙箱命令", out)


class TestSandboxCLIUnknown(unittest.TestCase):
    """完全不在命令列表里仍然报「未知沙箱命令」（不误伤）。"""

    def test_unknown_command_still_reports_unknown(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dispatch_sandbox(["foobar"])
        out = buf.getvalue()
        self.assertIn("未知沙箱命令: foobar", out)
        self.assertIn("支持: on/off/status/lang", out)


class TestSandboxCLIWithArgs(unittest.TestCase):
    """带参数时功能本体正常工作（回归保护）。"""

    def test_run_with_code_executes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dispatch_sandbox(["run", "print(6*7)"])
        out = buf.getvalue()
        self.assertIn("42", out)

    def test_scan_with_file_works(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write('import os\nos.system("ls")\n')
            path = f.name
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                _dispatch_sandbox(["scan", path])
            out = buf.getvalue()
            self.assertIn("安全扫描", out)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
