# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R39：CLI 健壮性 IV（unittest）

运行：
  python3 -m unittest tests.test_round43_cli4 -v
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


class CliDeepTest(unittest.TestCase):
    def test_no_extension_file(self):
        # 无扩展名代码文件（Dockerfile/rc 类）
        d = tempfile.mkdtemp(prefix="tf_r43_")
        try:
            open(os.path.join(d, "startup"), "w").write("print('hi')\n")
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, "无扩展名文件崩溃")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_mixed_binary_text(self):
        # 二进制内容 + 文本混合
        d = tempfile.mkdtemp(prefix="tf_r43_")
        try:
            with open(os.path.join(d, "mix.py"), "wb") as f:
                f.write(b"import os\n\x00\x01\x02" + b"cmd=input()\nos.system(cmd)\n")
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, "混合文件崩溃")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_deep_nested_dir(self):
        # 深层嵌套目录（20 层）
        d = tempfile.mkdtemp(prefix="tf_r43_")
        try:
            cur = d
            for i in range(20):
                cur = os.path.join(cur, "d%d" % i)
            os.makedirs(cur)
            with open(os.path.join(cur, "deep.py"), "w") as f:
                f.write("import os\ncmd = input()\nos.system(cmd)\n")
            code, out = run_cli(d, "--security")
            self.assertNotIn("Traceback", out, "深目录崩溃")
            self.assertIn("命令注入", out, "深目录漏检")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_all_checks_multilang(self):
        # --all-checks 多语言混合
        d = tempfile.mkdtemp(prefix="tf_r43_")
        try:
            open(os.path.join(d, "a.py"), "w").write("import os\nos.system('id')\n")
            open(os.path.join(d, "b.js"), "w").write("eval(code)\n")
            open(os.path.join(d, "c.java"), "w").write("Runtime.getRuntime().exec(cmd);\n")
            code, out = run_cli(d, "--all-checks", timeout=90)
            self.assertNotIn("Traceback", out, "all-checks 崩溃")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()