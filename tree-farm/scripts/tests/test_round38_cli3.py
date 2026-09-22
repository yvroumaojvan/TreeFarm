# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R24：CLI/沙箱健壮性补测（unittest）

运行：
  python3 -m unittest tests.test_round38_cli3 -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE_FARM = os.path.join(SCRIPTS, "tree_farm.py")


def run_cli(*args, timeout=60):
    p = subprocess.run([sys.executable, TREE_FARM] + list(args),
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class CliEdgeTest(unittest.TestCase):
    def test_empty_content_file(self):
        # 空文件不崩
        d = tempfile.mkdtemp(prefix="tf_r38_")
        try:
            open(os.path.join(d, "a.py"), "w").close()
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, f"空文件崩溃: {out[:200]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_weird_filename(self):
        # 特殊字符文件名
        d = tempfile.mkdtemp(prefix="tf_r38_")
        try:
            with open(os.path.join(d, "a b(!).py"), "w") as f:
                f.write("import os\ncmd = input()\nos.system(cmd)\n")
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, f"特殊文件名崩溃")
            self.assertIn("命令注入", out, f"特殊文件名漏检")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_one_line_huge(self):
        # 单行 5MB（ReDoS 护栏验证）
        d = tempfile.mkdtemp(prefix="tf_r38_")
        try:
            with open(os.path.join(d, "huge.py"), "w") as f:
                f.write("x = '%s'\n" % ("a" * 5_000_000))
            code, out = run_cli(d, "--security", timeout=90)
            self.assertNotIn("Traceback", out, f"超长行崩溃")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class SandboxEdgeTest(unittest.TestCase):
    def test_sandbox_not_enabled(self):
        # 沙箱未开启时友好提示
        d = tempfile.mkdtemp(prefix="tf_r38_")
        try:
            with open(os.path.join(d, "a.py"), "w") as f:
                f.write("print(1)\n")
            code, out = run_cli(d, "--sandbox", "run")
            self.assertNotIn("Traceback", out, f"沙箱未开启崩溃: {out[:200]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()