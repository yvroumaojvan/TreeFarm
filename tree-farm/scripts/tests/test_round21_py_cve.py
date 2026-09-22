# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R2：Python 安全地狱 II（真实 CVE 变体）（unittest）

目标：攻更刁钻的漏洞变体（CVE 实战模式）：命令注入转义绕过、动态表名 SQL、
协议走私 SSRF、zip 穿越、弱哈希、JWT/ghp 密钥格式等。

运行：
  python3 -m unittest tests.test_round21_py_cve -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r21_")
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


class CmdInjectionCveTest(unittest.TestCase):
    """命令注入 · CVE 变体"""

    def test_shell_env_inject(self):
        # 环境变量注入命令（CVE 常见：把用户输入拼进 env）
        issues = issues_of('''\
import subprocess
def run(host):
    return subprocess.Popen(f"ping -c 1 {host}", shell=True)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"f-string shell 漏报: {issues}")

    def test_double_quote_escape(self):
        # 引号转义绕过形态
        issues = issues_of('''\
import os
def backup(name):
    os.system('tar czf /backup/{} data'.format(name))
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"format 进 shell 漏报: {issues}")

    def test_os_popen_user(self):
        issues = issues_of('''\
import os
def list_dir(path):
    return os.popen("ls " + path).read()
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"os.popen 拼接漏报: {issues}")

    def test_curl_var(self):
        # curl 拼接（真实场景：下载功能）
        issues = issues_of('''\
import subprocess
def download(url):
    subprocess.call("wget " + url, shell=True)
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"wget 拼接漏报: {issues}")


class SqlCveTest(unittest.TestCase):
    """SQL 注入 · CVE 变体"""

    def test_dynamic_table_name(self):
        # 动态表名（ORM 不覆盖）
        issues = issues_of('''\
def query(conn, table):
    return conn.execute("SELECT * FROM " + table + " WHERE id=1")
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"动态表名漏报: {issues}")

    def test_join_build_sql(self):
        issues = issues_of('''\
def search(cur, kw):
    where = " AND name LIKE '%" + kw + "%'"
    sql = "SELECT * FROM users WHERE 1=1" + where
    cur.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"分步拼接漏报: {issues}")

    def test_fstring_multiline_sql(self):
        issues = issues_of('''\
def q(cur, uid):
    sql = f"""
        SELECT * FROM users
        WHERE id = {uid}
    """
    cur.execute(sql)
''', "SQL注入")
        self.assertTrue(len(issues) >= 1, f"多行 f-string SQL 漏报: {issues}")


class SsrfCveTest(unittest.TestCase):
    """SSRF · 协议走私/变体"""

    def test_file_protocol(self):
        # file:// 协议走私（读本地文件）
        issues = issues_of('''\
import requests
def fetch(u):
    return requests.get("file:///etc/passwd" if u.startswith("file") else u)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"协议走私漏报: {issues}")

    def test_redirect_follow(self):
        issues = issues_of('''\
import urllib.request
def open_url(target):
    return urllib.request.urlopen("https://proxy.example.com/" + target)
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"前缀拼接漏报: {issues}")


class DeserCveTest(unittest.TestCase):
    """反序列化 · CVE 变体"""

    def test_yaml_full_tag(self):
        issues = issues_of('''\
import yaml
def parse(data):
    return yaml.load(data, Loader=yaml.FullLoader)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"FullLoader 漏报: {issues}")

    def test_shelve_user_file(self):
        issues = issues_of('''\
import shelve
def load(db_path):
    return shelve.open(db_path)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"shelve 漏报: {issues}")

    def test_marshal_loads(self):
        issues = issues_of('''\
import marshal
def load(b):
    return marshal.loads(b)
''', "不安全反序列化")
        self.assertTrue(len(issues) >= 1, f"marshal 漏报: {issues}")


class PathCveTest(unittest.TestCase):
    """路径穿越 · CVE 变体"""

    def test_zip_traversal(self):
        # zip 解压穿越（CVE-2022 类，规则类型为 Zip Slip）
        issues = issues_of('''\
import zipfile
def unzip(archive, dest):
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            z.extract(name, dest)
''', "Zip Slip")
        self.assertTrue(len(issues) >= 1, f"zip extract 漏报: {issues}")

    def test_os_remove_concat(self):
        issues = issues_of('''\
import os
def delete(fname):
    os.remove("/data/tmp/" + fname)
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"os.remove 拼接漏报: {issues}")

    def test_path_join_input(self):
        issues = issues_of('''\
import os
def serve(name):
    return open(os.path.join("static", input())).read()
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"join+input 漏报: {issues}")


class HardcodedCveTest(unittest.TestCase):
    """硬编码凭据 · 更多格式"""

    def test_ghp_token(self):
        issues = issues_of('''\
GITHUB_TOKEN = "ghp_X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0"
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"ghp_ 漏报: {issues}")

    def test_jwt_token(self):
        issues = issues_of('''\
AUTH = "eyJX0X0X0X0X0X0X0X0X0X0X0.X0X0X0X0X0X0X0X0X0X0X0.X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0X0"
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"JWT 漏报: {issues}")

    def test_aws_key(self):
        issues = issues_of('''\
AKIAX0X0X0X0X0X0X0X0
''', "硬编码凭据")
        self.assertTrue(len(issues) >= 1, f"AWS AKIA 漏报: {issues}")


class WeakHashTest(unittest.TestCase):
    """弱哈希/弱加密"""

    def test_sha1(self):
        # sha1 用于密码哈希（弱哈希语境，规则类型为"弱哈希"）
        issues = issues_of('''\
import hashlib
def digest(password):
    return hashlib.sha1(password.encode()).hexdigest()
''', "弱哈希")
        self.assertTrue(len(issues) >= 1, f"sha1 密码漏报: {issues}")

    def test_md5_password(self):
        issues = issues_of('''\
import hashlib
def hash_pwd(password):
    return hashlib.md5(password.encode()).hexdigest()
''', "弱哈希")
        self.assertTrue(len(issues) >= 1, f"md5 密码漏报: {issues}")

    def test_safe_sha256(self):
        issues = issues_of('''\
import hashlib
def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()
''', "弱哈希")
        self.assertEqual([], issues, f"sha256 误报: {issues}")


class SafeGuardTest(unittest.TestCase):
    """误报豁免"""

    def test_safe_param_bind(self):
        issues = issues_of('''\
def q(cur, uid):
    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
''', "SQL注入")
        self.assertEqual([], issues, f"参数化误报: {issues}")

    def test_sha256_ok(self):
        issues = issues_of('''\
import hashlib
def sign(data):
    return hashlib.sha256(data).hexdigest()
''', "弱加密算法")
        self.assertEqual([], issues, f"sha256 误报: {issues}")


if __name__ == "__main__":
    unittest.main()
