# -*- coding: utf-8 -*-
"""
树场 v4.9.8 C/C++ 性能与边界稳健性测试（第5轮，unittest，零依赖）

覆盖：
  - 残缺 #if 0（无 #endif 截断）内函数不得提取
  - 空文件 / 纯注释 / 纯声明 不崩溃、零提取
  - 超长点链调用（500 段）不卡死、不报错（ReDoS 防线）
  - 大文件性能上界（300 函数 ~5000 行 < 3s）
  - 超长无闭合引号/模板不崩溃
"""
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.parser import _strip_c_noise, _c_defs, extract_c_call_graph  # noqa: E402


class RobustnessTest(unittest.TestCase):
    def _defs_of(self, content):
        return {n for n, _, _, _ in _c_defs(_strip_c_noise(content))}

    def test_truncated_if0_removed(self):
        # 无 #endif 的截断 #if 0 块 → 内部函数不得提取
        content = "#if 0\nint dead(void) { return 1; }\nint dead2(void) { return 2; }\n"
        self.assertEqual(set(), self._defs_of(content))

    def test_if0_with_endif_removed(self):
        content = "#if 0\nint dead(void) { return 1; }\n#endif\nint live(void) { return 0; }\n"
        names = self._defs_of(content)
        self.assertNotIn("dead", names)
        self.assertIn("live", names)

    def test_empty_file(self):
        self.assertEqual([], _c_defs(_strip_c_noise("")))
        self.assertEqual(([], []), extract_c_call_graph(self._tmp("empty.c", "")))

    def test_comment_only(self):
        content = "/* only comment\n * line2\n */\n// done\n"
        self.assertEqual(set(), self._defs_of(content))

    def test_declarations_only(self):
        content = "int decl_only(int x);\nvoid another(void);\n"
        self.assertEqual(set(), self._defs_of(content))

    def test_unclosed_string_no_crash(self):
        content = 'char *s = "' + "a" * 10000 + "\nint main(void) { return 0; }\n"
        names = self._defs_of(content)
        self.assertIn("main", names)

    def test_mega_dot_chain_no_hang(self):
        # 500 段点链调用（嵌套星号 ReDoS 探测）：必须在 2s 内完成且不崩
        content = "int main(void) {\n    int x = " + \
                  ".".join("item%d" % i for i in range(500)) + "(\n    return 0;\n}\n"
        t0 = time.time()
        calls, _ = extract_c_call_graph(self._tmp("chain.c", content))
        self.assertLess(time.time() - t0, 2.0)
        self.assertTrue(any("item0" in c for c in calls))

    def test_big_file_performance(self):
        # 5000 行 / 300 函数：strip+defs+calls 全流程 < 3s
        parts = ["#include <stdio.h>\n\n"]
        for i in range(300):
            parts.append(f"int func_{i}(int a, int b) {{\n")
            parts.append("    int x = a + b;\n")
            parts.append("    for (int j = 0; j < a; j++) x += helper_a(j);\n")
            parts.append("    if (x % 2) x -= helper_b(); else x += 1;\n")
            parts.append("    return x;\n}\n\n")
        parts.append("int helper_a(int v) { return v * 2; }\n\n")
        parts.append("int helper_b(void) { return 3; }\n\n")
        parts.append("int main(void) { return func_0(1, 2); }\n")
        content = "".join(parts)
        f = self._tmp("big.c", content)
        t0 = time.time()
        names = {n for n, _, _, _ in _c_defs(_strip_c_noise(content))}
        calls, _ = extract_c_call_graph(f)
        dt = time.time() - t0
        self.assertLess(dt, 3.0, f"大文件应在 3s 内: {dt:.2f}s")
        self.assertGreaterEqual(len(names), 300)
        self.assertTrue(calls)

    def test_decl_regex_no_redos(self):
        # __libunwind_config.h 事件复盘：声明正则 (?:\s*[*&]+\s*|\s+)+
        # 嵌套量词在长连续空白上指数回溯导致卡死（第6轮实锤）。
        # 回归防护：大量 #define + 续行空白 的文件必须在 1s 内完成。
        content = "#if defined(__arm__) && !defined(__USING_SJLJ_EXCEPTIONS__) && \\\n" + \
                  "    !defined(__ARM_DWARF_EH__) && !defined(__SEH__)\n" + \
                  "#define _LIBUNWIND_HIGHEST_DWARF_REGISTER_X86 8\n" + \
                  ("#define _LIBUNWIND_CONTEXT_SIZE_LONG " + "  ".join(str(i) for i in range(80)) + "\n") * 50
        t0 = time.time()
        calls, _ = extract_c_call_graph(self._tmp("redos2.h", content))
        self.assertLess(time.time() - t0, 1.0, "声明正则不得灾难性回溯")

    @staticmethod
    def _tmp(name, content):
        d = tempfile.mkdtemp(prefix="treefarm_v498p_")
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path


if __name__ == "__main__":
    unittest.main()