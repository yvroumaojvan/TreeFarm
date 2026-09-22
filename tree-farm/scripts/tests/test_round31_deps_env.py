# -*- coding: utf-8 -*-
"""
树场 35 轮测试 · R14：供应链/.env 密钥泄露（unittest，新文件类型支持）

运行：
  python3 -m unittest tests.test_round31_deps_env -v
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, name: str = "app.py") -> str:
    """建临时文件并保证目标文件名（.env/requirements.txt 依赖文件名触发）"""
    d = tempfile.mkdtemp(prefix="tf_r31_")
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class EnvFileTest(unittest.TestCase):
    def test_env_with_secret(self):
        # .env 含敏感 key → 报
        path = make_file('''\
# 数据库配置
DB_HOST=localhost
DB_USER=admin
API_KEY=sk-proj-9f8e7d6c5b4a3928abc
JWT_SECRET=superlongsecretvalue123
DEBUG=false
''', ".env")
        try:
            issues = detect_security_issues([path])["issues"]
            types = {i["type"] for i in issues}
            self.assertIn("密钥泄露", types, f"env 密钥漏报: {issues}")
            hits = [i for i in issues if "API_KEY" in i["code"] or "JWT_SECRET" in i["code"]]
            self.assertTrue(len(hits) >= 1, f"API_KEY/JWT 未报: {issues}")
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_env_no_sensitive_ok(self):
        # .env 无敏感配置 → 不报
        path = make_file('''\
# 普通配置
DEBUG=true
LOG_LEVEL=info
PORT=8080
''', ".env")
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertEqual([], issues, f"普通 env 误报: {issues}")
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


class RequirementsTest(unittest.TestCase):
    def test_unpinned_req(self):
        # requirements.txt 未钉版本 → 供应链风险
        path = make_file('''\
flask>=2.0
requests
numpy==1.26.4
# 注释行
-r base.txt
''', "requirements.txt")
        try:
            issues = detect_security_issues([path])["issues"]
            found = [i for i in issues if i["type"] == "供应链风险"]
            self.assertTrue(len(found) >= 1, f"未钉版本漏报: {issues}")
            self.assertTrue(all("flask" in i["code"] or "requests" in i["code"] for i in found),
                            f"应只报 flask/requests: {found}")
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def test_pinned_ok(self):
        path = make_file('''\
flask==2.3.2
numpy==1.26.4
''', "requirements.txt")
        try:
            issues = detect_security_issues([path])["issues"]
            self.assertEqual([], issues, f"钉版本误报: {issues}")
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()