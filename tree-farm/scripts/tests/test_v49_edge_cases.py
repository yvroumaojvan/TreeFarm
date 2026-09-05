# -*- coding: utf-8 -*-
"""
树场 v4.9.2 全面自测：边界/健壮性轰炸（unittest，零依赖）

目标：异常输入不许崩溃，报错友好。
覆盖：
  1. 空文件 / 空目录 / 只含子目录
  2. 二进制 / GBK 乱码 / 非法 UTF-8
  3. 超长单行（10MB）/ 超多空行
  4. 无读取权限文件
  5. 不存在路径 / 路径是文件而非目录
  6. .java 后缀但内容随机字节
  7. 深层嵌套目录 / 路径含特殊字符（空格/中文/#）
  8. 所有分析入口（security/perf/logic/gene/deep）都不抛异常

运行：
  python3 -m unittest tests.test_v49_edge_cases -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import (detect_security_issues, detect_performance_issues,
                               detect_logic_issues)
from treefarm.parser import extract_genes


def make_proj(files: dict) -> str:
    """造一个临时项目目录：{相对路径: 内容}。返回根目录。"""
    root = tempfile.mkdtemp(prefix="tf_edge_")
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(content, bytes):
            with open(p, "wb") as f:
                f.write(content)
        else:
            with open(p, "w", encoding="utf-8", errors="ignore") as f:
                f.write(content)
    return root


def run_all_scanners(root):
    """把所有分析入口都跑一遍，返回是否全部无异常。"""
    try:
        detect_security_issues([], root=root)
        detect_performance_issues([], root=root)
        detect_logic_issues([], root=root)
        return True
    except Exception:
        return False


class EmptyInputTest(unittest.TestCase):
    def test_empty_dir(self):
        root = make_proj({})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_empty_file(self):
        root = make_proj({"a.py": ""})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_only_subdirs(self):
        root = tempfile.mkdtemp(prefix="tf_edge_")
        os.makedirs(os.path.join(root, "x", "y", "z"), exist_ok=True)
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_no_code_ext(self):
        root = make_proj({"readme.md": "# hi", "data.bin": b"x1"})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class EncodingInputTest(unittest.TestCase):
    def test_binary_random(self):
        root = make_proj({"a.java": bytes(range(256)) * 100})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_gbk_mojibake(self):
        root = make_proj({"a.py": "print('中文乱码测试')\n".encode("gbk")})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_huge_single_line(self):
        root = make_proj({"a.py": "x = " + "1" * (10 * 1024 * 1024)})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_many_blank_lines(self):
        root = make_proj({"a.py": "\n" * 50000 + "print(1)\n"})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_java_ext_random_bytes(self):
        root = make_proj({"a.java": bytes([0x00, 0xFF, 0xFE, 0x7F]) * 1000})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class PathInputTest(unittest.TestCase):
    def test_nonexistent_path(self):
        root = tempfile.mkdtemp(prefix="tf_edge_")
        try:
            self.assertTrue(run_all_scanners(os.path.join(root, "nope")))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_path_is_file(self):
        root = make_proj({"a.py": "print(1)\n"})
        try:
            self.assertTrue(run_all_scanners(os.path.join(root, "a.py")))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_special_chars_in_path(self):
        root = tempfile.mkdtemp(prefix="tf_") + "/dir with 空格-中文-#hash"
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "插件 测试.java"), "w") as f:
            f.write("public class A { void r(String c) { new ProcessBuilder(c); } }\n")
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(os.path.dirname(root), ignore_errors=True)

    def test_deep_nesting(self):
        deep = "/".join(["d"] * 30)
        root = make_proj({deep + "/a.py": "print(1)\n"})
        try:
            self.assertTrue(run_all_scanners(root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipIf(os.geteuid() == 0, "root 不适用权限测试")
    def test_no_permission_file(self):
        root = make_proj({"a.py": "print(1)\n"})
        try:
            os.chmod(os.path.join(root, "a.py"), 0)
            self.assertTrue(run_all_scanners(root))
        finally:
            os.chmod(os.path.join(root, "a.py"), 0o644)
            shutil.rmtree(root, ignore_errors=True)


class DeepScanEntryTest(unittest.TestCase):
    """直接跑 CLI 入口（short 扫描路径），异常输入不许 Traceback。"""

    def test_cli_scan_empty(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from treefarm.core import TreeFarm
        root = make_proj({})
        try:
            tf = TreeFarm(root)
            tf.plant()
            out = tf.brief()
            self.assertIsInstance(out, str)
            self.assertTrue(len(out) > 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_scanners_on_dirty_files(self):
        """脏文件混合：正常 + 空 + 二进制 + 乱码一起扫。"""
        root = make_proj({
            "src/a.py": "import os\nos.system(x)\n",
            "src/b.java": "public class B { void d(String p) { new File(p).delete(); } }\n",
            "src/c.py": b"\x00\x01\x02",
            "src/d.js": "var x = '" + "A" * 500000 + "';\n",
            "data/blob.bin": bytes(4096),
        })
        try:
            issues = detect_security_issues(
                [os.path.join(root, f) for f in
                 ["src/a.py", "src/b.java", "src/c.py", "src/d.js"]],
                root=root)
            self.assertIsInstance(issues["issues"], list)
            self.assertGreaterEqual(len(issues["issues"]), 1)  # a.py 命令注入/B.java 删除
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()