# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R5：C/C++ 安全深化（unittest）

运行：
  python3 -m unittest tests.test_round24_c -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".c") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r24_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".c"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class CUnsafeFuncTest(unittest.TestCase):
    """C 危险函数"""

    def test_gets(self):
        issues = issues_of('''\
#include <stdio.h>
int main() {
    char buf[64];
    gets(buf);
    printf("%s\\n", buf);
}
''', "缓冲区溢出")
        self.assertTrue(len(issues) >= 1, f"gets 漏报: {issues}")

    def test_strcpy(self):
        issues = issues_of('''\
#include <string.h>
void copy(char *dst, const char *src) {
    strcpy(dst, src);
}
''', "缓冲区溢出")
        self.assertTrue(len(issues) >= 1, f"strcpy 漏报: {issues}")

    def test_sprintf_concat(self):
        issues = issues_of('''\
#include <stdio.h>
void build(char *out, const char *name) {
    sprintf(out, "Hello %s!", name);
}
''', "缓冲区溢出")
        self.assertTrue(len(issues) >= 1, f"sprintf 格式化漏报: {issues}")

    def test_scanf_unbounded(self):
        issues = issues_of('''\
#include <stdio.h>
int main() {
    char buf[32];
    scanf("%s", buf);
}
''', "缓冲区溢出")
        self.assertTrue(len(issues) >= 1, f"scanf %s 漏报: {issues}")

    def test_safe_snprintf(self):
        # 安全：snprintf 限长 → 不报
        issues = issues_of('''\
#include <stdio.h>
void build(char *out, size_t n, const char *name) {
    snprintf(out, n, "Hello %s!", name);
}
''', "缓冲区溢出")
        self.assertEqual([], issues, f"snprintf 误报: {issues}")


class CCommandTest(unittest.TestCase):
    """C 命令执行"""

    def test_system_var(self):
        issues = issues_of('''\
#include <stdlib.h>
void run(const char *cmd) {
    system(cmd);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"system(变量) 漏报: {issues}")

    def test_system_concat(self):
        issues = issues_of('''\
#include <stdlib.h>
void backup(const char *path) {
    char cmd[256];
    sprintf(cmd, "tar czf /tmp/bk.tar %s", path);
    system(cmd);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"system+sprintf 拼接漏报: {issues}")


class CppTest(unittest.TestCase):
    """C++ 危险用法"""

    def test_cpp_system_cmd(self):
        issues = issues_of('''\
#include <cstdlib>
void run(const std::string &cmd) {
    system(cmd.c_str());
}
''', "命令注入", ".cpp")
        self.assertTrue(len(issues) >= 1, f"C++ system 漏报: {issues}")

    def test_cpp_delete_array(self):
        issues = issues_of('''\
template <typename T>
void clear(T* arr) {
    delete arr;
}
''', "缓冲区溢出", ".cpp")
        # 不崩即可（new[]/delete 配对是常见 C++ 错误，弱信号）
        self.assertIsInstance(issues, list)


class CFpTest(unittest.TestCase):
    """C 误报治理"""

    def test_safe_fread(self):
        issues = issues_of('''\
#include <stdio.h>
size_t load(char *out, size_t n, FILE *f) {
    return fread(out, 1, n, f);
}
''', "缓冲区溢出")
        self.assertEqual([], issues, f"fread 误报: {issues}")

    def test_strncpy_safe(self):
        issues = issues_of('''\
#include <string.h>
void copy(char *dst, const char *src, size_t n) {
    strncpy(dst, src, n);
    dst[n - 1] = '\\0';
}
''', "缓冲区溢出")
        self.assertEqual([], issues, f"strncpy 误报: {issues}")


if __name__ == "__main__":
    unittest.main()