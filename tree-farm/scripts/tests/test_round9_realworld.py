# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 9 轮：真实项目回归（NetBell 实测驱动）（unittest，零依赖）

来源：用插件扫 NetBell 真实项目（31 文件 Java+Python）发现的 2 类误报：
  1. Runtime.exec(new String[]{"su","-c","id"}) 纯字面量数组被误报命令执行
     （RootKeeper.java:19 实测）——数组全常量时无动态成分
  2. os.path.join(root, fn) 遍历目录变量被误报路径遍历（tests/test_manifest.py 实测）

修复（r9，analysis.py）：
  A. _scan_java_security 命令执行规则：new String[]{...} 去掉引号后只剩
     new/String/[]{}, 等关键字 → 纯常量数组，豁免
  B. join 常见变量名单移除 fn（os.walk 遍历惯用名，误报源）

运行：
  python3 -m unittest tests.test_round9_realworld -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".java") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_r9_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".java"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class JavaLiteralArrayExemptTest(unittest.TestCase):
    """Java 纯字面量数组命令执行豁免"""

    def test_literal_array_no_fp(self):
        # NetBell RootKeeper:19 形态：全常量数组 → 不报
        issues = issues_of('''\
public class RootKeeper {
    public void check() throws Exception {
        Process p = Runtime.getRuntime().exec(new String[]{"su", "-c", "id"});
        p.waitFor();
    }
}
''', "命令执行")
        self.assertEqual([], issues, f"纯字面量数组误报: {issues}")

    def test_dynamic_array_still_reported(self):
        # NetBell RootKeeper:70 形态：数组含变量 → 必报
        issues = issues_of('''\
public class RootKeeper {
    public void run(String cmd) throws Exception {
        Process p = Runtime.getRuntime().exec(new String[]{"su", "-c", cmd});
        p.waitFor();
    }
}
''', "命令执行")
        self.assertTrue(len(issues) >= 1, f"动态数组漏报: {issues}")


class WalkLoopVarExemptTest(unittest.TestCase):
    """os.walk 遍历变量 join 豁免（fn 移出名单）"""

    def test_walk_join_no_fp(self):
        # NetBell test_manifest.py 形态：os.walk 遍历目录 → 不报
        issues = issues_of('''\
import os


def collect(root):
    out = []
    for root, dirs, files in os.walk(root):
        for fn in files:
            out.append(os.path.join(root, fn))
    return out
''', "路径遍历", ".py")
        self.assertEqual([], issues, f"遍历变量误报: {issues}")

    def test_fname_join_still_reported(self):
        # 明确的用户输入文件名 fname → 仍报
        issues = issues_of('''\
import os


def read(file_name):
    path = os.path.join("/var/data", file_name)
    return open(path).read()
''', "路径遍历", ".py")
        self.assertTrue(len(issues) >= 1, f"fname join 漏报: {issues}")


if __name__ == "__main__":
    unittest.main()
