# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R31：编码边界（unittest）

运行：
  python3 -m unittest tests.test_round40_encoding -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


class EncodingEdgeTest(unittest.TestCase):
    def test_unicode_content(self):
        # 中文/emoji 代码内容不崩
        d = tempfile.mkdtemp(prefix="tf_r40_")
        try:
            p = os.path.join(d, "测试_emoji_😀.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# 中文注释\nimport os\ncmd = input()\nos.system(cmd)\n")
            res = detect_security_issues([p], root=d)
            self.assertTrue(res["total"] >= 1, "Unicode 文件漏检")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_bom_file(self):
        # UTF-8 BOM 文件
        d = tempfile.mkdtemp(prefix="tf_r40_")
        try:
            p = os.path.join(d, "bom.py")
            with open(p, "wb") as f:
                f.write(b"\xef\xbb\xbf" + "import os\nos.system('id')\n".encode("utf-8"))
            detect_security_issues([p], root=d)  # 不崩即可
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_gbk_binary(self):
        # GBK 中文 + 无效字节
        d = tempfile.mkdtemp(prefix="tf_r40_")
        try:
            p = os.path.join(d, "gbk.py")
            with open(p, "wb") as f:
                f.write("print('中文测试')\n".encode("gbk", errors="ignore") + b"\xff\xfe\x00")
            detect_security_issues([p], root=d)  # errors=ignore 不崩
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_empty_and_whitespace(self):
        d = tempfile.mkdtemp(prefix="tf_r40_")
        try:
            p = os.path.join(d, "ws.py")
            with open(p, "w") as f:
                f.write("\n\n   \n\t\n")
            res = detect_security_issues([p], root=d)
            self.assertEqual(0, res["total"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()