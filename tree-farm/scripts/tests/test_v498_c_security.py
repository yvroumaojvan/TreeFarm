# -*- coding: utf-8 -*-
"""
树场 v4.9.8 C/C++ 安全规则测试套件（第7轮，unittest，零依赖）

覆盖 _scan_c_security：
  - system("... " + var) / system(var) 命令注入 → 必报
  - system("date") 纯静态 → 不报
  - gets(buf) → 必报（无边界）
  - strcpy(dst, src_var) / strcat → 报；strcpy(dst, "const") → 不报
  - sprintf(buf, "%s", var) → 报；sprintf(buf, "fixed") → 不报
  - scanf("%s", buf) → 报；scanf("%d") → 不报
  - 硬编码 api_key/password → 报
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def c_issues(content):
    fd, path = tempfile.mkstemp(suffix=".c", prefix="treefarm_v498s_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        return detect_security_issues([path])["issues"]
    finally:
        os.unlink(path)


class CmdInjectionCTest(unittest.TestCase):
    def test_system_concat_reported(self):
        issues = c_issues('''\
#include <stdlib.h>
void run(char *cmd) {
    char buf[128];
    sprintf(buf, "ls -la %s", cmd);
    system(buf);
}
''')
        types = {i["type"] for i in issues}
        self.assertIn("命令注入", types, issues)
        self.assertIn("缓冲区溢出", types, issues)

    def test_system_static_not_reported(self):
        issues = c_issues('''\
#include <stdlib.h>
int main(void) {
    system("date");
    return 0;
}
''')
        self.assertEqual([], issues, issues)

    def test_system_variable_reported(self):
        issues = c_issues('''\
#include <stdlib.h>
void exec_cmd(const char *user_cmd) {
    system(user_cmd);
}
''')
        self.assertTrue(any(i["type"] == "命令注入" for i in issues), issues)


class BufferOverflowCTest(unittest.TestCase):
    def test_gets_reported(self):
        issues = c_issues('''\
#include <stdio.h>
int main(void) {
    char buf[128];
    gets(buf);
    return 0;
}
''')
        self.assertTrue(any(i["type"] == "缓冲区溢出"
                            and "gets" in i["desc"] for i in issues), issues)

    def test_strcpy_dynamic_reported(self):
        issues = c_issues('''\
#include <string.h>
void copy(char *dst, const char *src) {
    strcpy(dst, src);
}
''')
        self.assertTrue(any("strcpy" in i["desc"] for i in issues), issues)

    def test_strcpy_static_not_reported(self):
        issues = c_issues('''\
#include <string.h>
void init(char *dst) {
    strcpy(dst, "hello");
}
''')
        self.assertFalse(any("strcpy" in i["desc"] for i in issues), issues)

    def test_sprintf_dynamic_reported(self):
        issues = c_issues('''\
#include <stdio.h>
void fmt(char *buf, int n) {
    sprintf(buf, "value: %d", n);
}
''')
        self.assertTrue(any("sprintf" in i["desc"] for i in issues), issues)

    def test_sprintf_static_not_reported(self):
        issues = c_issues('''\
#include <stdio.h>
void hello(char *buf) {
    sprintf(buf, "hello");
}
''')
        self.assertFalse(any("sprintf" in i["desc"] for i in issues), issues)

    def test_scanf_string_reported(self):
        issues = c_issues('''\
#include <stdio.h>
int main(void) {
    char name[32];
    scanf("%s", name);
    return 0;
}
''')
        self.assertTrue(any("%s" in i["desc"] for i in issues), issues)

    def test_scanf_int_not_reported(self):
        issues = c_issues('''\
#include <stdio.h>
int main(void) {
    int x;
    scanf("%d", &x);
    return 0;
}
''')
        self.assertFalse(any("%s" in i["desc"] for i in issues), issues)


class HardcodedCredCTest(unittest.TestCase):
    def test_api_key_reported(self):
        issues = c_issues('''\
const char *api_key = "sk-abcdefghijklmnop1234567890";
int main(void) { return 0; }
''')
        self.assertTrue(any(i["type"] == "硬编码凭据" for i in issues), issues)


class NoFalsePositiveOnSafeCTest(unittest.TestCase):
    def test_clean_c_file(self):
        # 正常 C 代码：fgets/strncpy/snprintf 安全写法不报
        issues = c_issues('''\
#include <stdio.h>
#include <string.h>
int main(void) {
    char buf[64];
    fgets(buf, sizeof(buf), stdin);
    strncpy(buf, "safe", sizeof(buf) - 1);
    snprintf(buf, sizeof(buf), "%d", 42);
    return 0;
}
''')
        self.assertEqual([], issues, issues)


if __name__ == "__main__":
    unittest.main()