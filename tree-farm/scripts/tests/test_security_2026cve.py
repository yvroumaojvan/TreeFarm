# -*- coding: utf-8 -*-
"""
安全盲区终结战 · P6 测试护城河（2026 新形态 CVE 组）

覆盖 91 轮 R81-91 检出的形态：归档解压路径（tar/zip）、弱密钥/固定 IV、短密钥算法。
当前部分应红 → P6 规则落地后全绿。

运行：
  python3 -m unittest tests.test_security_2026cve -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_cve26_")
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


class ArchivePathTest(unittest.TestCase):
    """归档解压路径穿越（zip/tar slip & 遍历）"""

    def test_zip_extractall_dir(self):
        issues = issues_of('''\
import zipfile

def unzip(fn):
    with zipfile.ZipFile(fn) as z:
        z.extractall("/tmp/up/" + user_dir)
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"zip extractall 变量路径漏报: {issues}")

    def test_tar_extractall_dst(self):
        issues = issues_of('''\
import tarfile

def untar(fn):
    with tarfile.open(fn) as t:
        t.extractall(dest)
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"tar extractall 变量漏报: {issues}")

    def test_safe_extract_const(self):
        issues = issues_of('''\
import zipfile

def unzip(fn):
    with zipfile.ZipFile(fn) as z:
        z.extractall("/opt/fixed/dir")
''', "路径遍历")
        self.assertEqual([], issues, f"extractall 固定路径负例误报: {issues}")


class WeakCryptoTest(unittest.TestCase):
    """弱密钥 / 固定 IV / 短密钥算法"""

    def test_aes_fixed_iv(self):
        issues = issues_of('''\
from Crypto.Cipher import AES

def enc(data, key):
    iv = b"\\x00" * 16
    c = AES.new(key, AES.MODE_CBC, iv)
    return c.encrypt(data)
''', "弱加密算法")
        self.assertTrue(len(issues) >= 1, f"AES 固定 IV 漏报: {issues}")

    def test_des_weak_cipher(self):
        issues = issues_of('''\
from Crypto.Cipher import DES

def enc(data, key):
    c = DES.new(key, DES.MODE_ECB)
    return c.encrypt(data)
''', "弱加密算法")
        self.assertTrue(len(issues) >= 1, f"DES/ECB 漏报: {issues}")

    def test_rsa_short_key(self):
        issues = issues_of('''\
from Crypto.PublicKey import RSA

def gen():
    return RSA.generate(512)
''', "弱加密算法")
        self.assertTrue(len(issues) >= 1, f"RSA 512 短密钥漏报: {issues}")

    def test_safe_aes_gcm_no_fp(self):
        issues = issues_of('''\
from Crypto.Cipher import AES

def enc(data, key, nonce):
    c = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return c.encrypt(data)
''', "弱加密算法")
        self.assertEqual([], issues, f"AES-GCM 安全负例误报: {issues}")


if __name__ == "__main__":
    unittest.main()