# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R11：反序列化/SSRF/硬编码深水区（unittest）

运行：
  python3 -m unittest tests.test_round28_deser_ssrf -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r28_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".py"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class DeserDeepTest(unittest.TestCase):
    """反序列化深水区"""

    def test_pickle_import_alias(self):
        # import pickle as p; p.loads
        issues = issues_of('''\
import pickle as p
def load(data):
    return p.loads(data)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"pickle 别名漏报: {issues}")

    def test_pickle_inside_function(self):
        issues = issues_of('''\
def deserialize():
    import pickle
    return pickle.loads(get_input())
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"函数内导入漏报: {issues}")

    def test_restricted_pickle(self):
        # 限制级 pickle 也算风险提示
        issues = issues_of('''\
import pickle
def safe_load(b):
    return pickle.loads(b, fix_imports=False)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"restricted pickle 漏报: {issues}")

    def test_ast_literal_eval_ok(self):
        # ast.literal_eval 安全 → 不报
        issues = issues_of('''\
import ast
def parse(text):
    return ast.literal_eval(text)
''', "不安全反序列化")
        self.assertEqual([], issues, f"literal_eval 误报: {issues}")

    def test_yaml_loader_safe_ok(self):
        issues = issues_of('''\
import yaml
def parse(text):
    return yaml.load(text, Loader=yaml.SafeLoader)
''', "不安全反序列化")
        self.assertEqual([], issues, f"SafeLoader 误报: {issues}")


class SsrfDeepTest(unittest.TestCase):
    """SSRF 深水区"""

    def test_httpx_dynamic(self):
        issues = issues_of('''\
import httpx
def fetch(url):
    return httpx.get(url)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"httpx 变量漏报: {issues}")

    def test_aiohttp_dynamic(self):
        issues = issues_of('''\
import aiohttp
async def fetch(url):
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            return await r.text()
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"aiohttp 变量漏报: {issues}")

    def test_urllib3_dynamic(self):
        issues = issues_of('''\
import urllib3
def fetch(url):
    http = urllib3.PoolManager()
    return http.request("GET", url)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"urllib3 变量漏报: {issues}")

    def test_socket_direct(self):
        issues = issues_of('''\
import socket
def probe(target):
    s = socket.create_connection((target, 6379))
    return s
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"socket 直连漏报: {issues}")

    def test_requests_inner_ip(self):
        # 内网 IP 直连（SSRF 经典）
        issues = issues_of('''\
import requests
def ping():
    return requests.get("http://169.254.169.254/latest/meta-data/")
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"云元数据 IP 漏报: {issues}")

    def test_safe_fetch_https(self):
        issues = issues_of('''\
import requests
def ping():
    return requests.get("https://api.github.com/zen")
''', "SSRF")
        self.assertEqual([], issues, f"固定 HTTPS 误报: {issues}")


class HardcodedDeepTest(unittest.TestCase):
    """硬编码深水区"""

    def test_db_url_password(self):
        # 连接串内嵌密码
        issues = issues_of('''\
DB_URL = "postgresql://admin:X0X0X0X0X0@db.internal:5432/mydb"
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"连接串密码漏报: {issues}")

    def test_ssh_private_key(self):
        issues = issues_of('''\
KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEaX0X0X0X0X0X0X0X0X0X0
-----END RSA PRIVATE KEY-----"""
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"RSA 私钥漏报: {issues}")

    def test_slack_token(self):
        issues = issues_of('''\
SLACK = "xoxb-X0X0X0X0X0-X0X0X0X0X0X0X0X0X0X0X0X0X0X0"
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"Slack xoxb 漏报: {issues}")

    def test_placeholder_ok(self):
        issues = issues_of('''\
PASSWORD = os.environ.get("DB_PASSWORD", "changeme")
''', "硬编码凭据")
        self.assertEqual([], issues, f"环境变量误报: {issues}")


if __name__ == "__main__":
    unittest.main()