# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R41：跨文件深化 IV（unittest）

运行：
  python3 -m unittest tests.test_round45_cross4 -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def project_issues(files, itype=None):
    d = tempfile.mkdtemp(prefix="tf_r45_")
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


class CrossFileDeepTest(unittest.TestCase):
    def test_kwargs_passthrough(self):
        issues = project_issues({
            "a.py": "import b\ndef h(r):\n    b.exec_sql(sql=r.args.get(\"q\"))\n",
            "b.py": "def exec_sql(sql):\n    execute(sql)\n",
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"kwargs 传递漏报: {issues}")

    def test_import_inside_function(self):
        issues = project_issues({
            "a.py": "def h(r):\n    import b\n    b.run(r.args.get(\"c\"))\n",
            "b.py": "import os\ndef run(cmd):\n    os.system(cmd)\n",
        }, "命令注入")
        self.assertTrue(len(issues) >= 1, f"函数内 import 漏报: {issues}")

    def test_from_import_chain(self):
        issues = project_issues({
            "a.py": "from b import t\ndef h(r):\n    t(r.args.get(\"q\"))\n",
            "b.py": "from c import u\ndef t(v):\n    u(v)\n",
            "c.py": "def u(w):\n    execute(w)\n",
        }, "SQL注入")
        self.assertTrue(len(issues) >= 1, f"from 链漏报: {issues}")


class CrossFileSafeTest(unittest.TestCase):
    def test_safe_cross_constant(self):
        # 跨文件传常量：不得报 critical（r41 降级为 low 需人工确认可接受）
        issues = project_issues({
            "a.py": "import b\ndef h():\n    b.run(\"ls\")\n",
            "b.py": "import os\ndef run(cmd):\n    os.system(cmd)\n",
        }, "命令注入")
        self.assertFalse(any(i["severity"] == "critical" for i in issues),
                         f"跨文件常量不应报 critical: {issues}")


if __name__ == "__main__":
    unittest.main()
