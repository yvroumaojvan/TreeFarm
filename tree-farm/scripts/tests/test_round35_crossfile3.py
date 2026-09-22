# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R19：跨文件污点极限 III（unittest）

运行：
  python3 -m unittest tests.test_round35_crossfile3 -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def project_issues(files, itype=None):
    d = tempfile.mkdtemp(prefix="tf_r35_")
    try:
        paths = []
        for name, content in files.items():
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            paths.append(p)
        issues = detect_security_issues(paths, root=d)["issues"]
        if itype:
            return [i for i in issues if i["type"] == itype]
        return issues
    finally:
        shutil.rmtree(d, ignore_errors=True)


class CrossFileVariantTest(unittest.TestCase):
    """跨文件传递变体"""

    def test_return_passthrough(self):
        # b.entry 内部 return 传递到 c
        issues = project_issues({
            "a.py": "import b\ndef h(r):\n    b.entry(r.args.get(\"q\"))\n",
            "b.py": "import c\ndef entry(x):\n    return c.run(x)\n",
            "c.py": "def run(s):\n    execute(s)\n",
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"return 传递漏报: {issues}")

    def test_conditional_passthrough(self):
        issues = project_issues({
            "a.py": "import b\ndef h(r):\n    b.go(r.form.get(\"q\"))\n",
            "b.py": "import c\ndef go(x):\n    if x:\n        c.fire(x)\n",
            "c.py": "def fire(s):\n    execute(s)\n",
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"条件传递漏报: {issues}")

    def test_abbrev_source_cross_file(self):
        # req 缩写 + zip 压缩传递
        issues = project_issues({
            "a.py": "import b\ndef h(req):\n    b.handle(req.args.get(\"url\"))\n",
            "b.py": "import urllib.request\ndef handle(u):\n    return urllib.request.urlopen(u)\n",
        }, "SSRF")
        self.assertTrue(len(issues) >= 1, f"req 缩写跨文件 SSRF 漏报: {issues}")

    def test_three_layer_cmd(self):
        issues = project_issues({
            "a.py": "import b\ndef h(r):\n    b.t(r.args.get(\"c\"))\n",
            "b.py": "import c\ndef t(v):\n    c.u(v)\n",
            "c.py": "def u(w):\n    import os\n    os.system(w)\n",
        }, "命令注入")
        self.assertTrue(len(issues) >= 1, f"三层命令注入漏报: {issues}")


class CrossFileSafeTest(unittest.TestCase):
    """跨文件安全写法不报"""

    def test_safe_param_bind_cross(self):
        issues = project_issues({
            "a.py": "import b\ndef h(r):\n    b.find(r.args.get(\"id\"))\n",
            "b.py": "def find(i):\n    return execute(\"SELECT * FROM t WHERE id = ?\", (i,))\n",
        }, "SQL注入")
        self.assertEqual([], issues, f"跨文件参数化误报: {issues}")


if __name__ == "__main__":
    unittest.main()