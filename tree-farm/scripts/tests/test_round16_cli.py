# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 16 轮：CLI 健壮性（unittest，零依赖）

目标：验证 CLI 对边界输入的友好处理——缺参提示 / 不存在路径 / 空目录 /
畸形编码 / 超大文件 / 中文路径 / 二进制文件，不崩不 Traceback。

运行：
  python3 -m unittest tests.test_round16_cli -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tree_farm as tf  # noqa: E402

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE_FARM = os.path.join(SCRIPTS, "tree_farm.py")


def run_cli(*args, timeout=60):
    """跑 CLI 子进程，返回 (returncode, stdout+stderr)。"""
    p = subprocess.run([sys.executable, TREE_FARM] + list(args),
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class MissingArgTest(unittest.TestCase):
    def test_no_args(self):
        code, out = run_cli()
        self.assertNotEqual(code, 1, "无参数应显示用法而非崩溃")
        self.assertTrue(len(out) > 0)

    def test_sandbox_missing_arg(self):
        # v4.9.6 修复：--sandbox 缺参给友好提示
        d = tempfile.mkdtemp(prefix="tf_r16_")
        try:
            with open(os.path.join(d, "a.py"), "w") as f:
                f.write("x = 1\n")
            code, out = run_cli(d, "--sandbox", "run")
            self.assertIn("用法", out, f"缺参提示缺失: {out[:200]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class PathEdgeTest(unittest.TestCase):
    def test_nonexistent_path(self):
        code, out = run_cli("/nonexistent/path/xyz", "--security")
        self.assertNotIn("Traceback", out, f"不存在路径崩溃: {out[:300]}")
        self.assertTrue("不存在" in out or "未找到" in out or len(out) > 0)

    def test_empty_dir(self):
        d = tempfile.mkdtemp(prefix="tf_r16_")
        try:
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, f"空目录崩溃: {out[:300]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_chinese_path(self):
        d = tempfile.mkdtemp(prefix="测试中文目录_")
        try:
            with open(os.path.join(d, "测试.py"), "w", encoding="utf-8") as f:
                f.write("import os\ncmd = input()\nos.system(cmd)\n")
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, f"中文路径崩溃: {out[:300]}")
            self.assertIn("命令注入", out, f"中文路径漏检: {out[:300]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class BinaryFileTest(unittest.TestCase):
    def test_binary_file_no_crash(self):
        d = tempfile.mkdtemp(prefix="tf_r16_")
        try:
            # 二进制/畸形编码文件（含无效 UTF-8）
            with open(os.path.join(d, "bad.py"), "wb") as f:
                f.write(b"\xff\xfe\x00\x01binary\x80\x81data" * 100)
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, f"二进制文件崩溃: {out[:300]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class HugeFileTest(unittest.TestCase):
    def test_huge_line_no_crash(self):
        d = tempfile.mkdtemp(prefix="tf_r16_")
        try:
            # 超长行（10MB 单行，防 ReDoS 护栏验证）
            with open(os.path.join(d, "huge.py"), "w") as f:
                f.write("x = '" + "a" * 10_000_000 + "'\n")
            code, out = run_cli(d, "--security", timeout=90)
            self.assertNotIn("Traceback", out, f"超长行崩溃: {out[:300]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class UnknownCommandTest(unittest.TestCase):
    def test_unknown_flag(self):
        d = tempfile.mkdtemp(prefix="tf_r16_")
        try:
            with open(os.path.join(d, "a.py"), "w") as f:
                f.write("x = 1\n")
            code, out = run_cli(d, "--no-such-flag-xyz")
            self.assertNotIn("Traceback", out, f"未知参数崩溃: {out[:300]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
