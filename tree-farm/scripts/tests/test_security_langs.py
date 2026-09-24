# -*- coding: utf-8 -*-
"""
安全盲区终结战 · P0 测试护城河（B1：PHP/Go/Rust/Kotlin 安全扫描缺失）

每条规则 3 个变体（换函数名/写法）+ 1 个安全负例（参数化/白名单写法不得误报）。
当前这些扩展名不在 _SECURITY_EXTS → 全部应红（0 检出）→ 这是盲区的活证据；
P1-P4 扫描器落地后本文件必须全绿（变体过 = 泛化，负例过 = 无回归误报）。

运行：
  python3 -m unittest tests.test_security_langs -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".py") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_lang_")
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


class PhpSecurityTest(unittest.TestCase):
    """PHP 安全扫描（B1-P1）"""

    def test_sqli_direct_concat(self):
        issues = issues_of('''\
<?php
$id = $_GET['id'];
$r = mysqli_query($conn, "SELECT * FROM users WHERE id = $id");
''', "SQL注入", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP SQLi 直接拼接漏报: {issues}")

    def test_sqli_pdo_dot_concat(self):
        issues = issues_of('''\
<?php
$q = "SELECT * FROM users WHERE id = " . $_POST['id'];
$stmt = $pdo->query($q);
''', "SQL注入", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP PDO 点拼接 SQLi 漏报: {issues}")

    def test_sqli_heredoc_concat(self):
        issues = issues_of('''\
<?php
$name = $_REQUEST['name'];
$sql = "SELECT * FROM users WHERE name LIKE '%{$name}%'";
mysqli_query($conn, $sql);
''', "SQL注入", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP heredoc 插值 SQLi 漏报: {issues}")

    def test_cmdi_system(self):
        issues = issues_of('''\
<?php
$cmd = $_POST['cmd'];
system("ping " . $cmd);
''', "命令注入", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP system 命令注入漏报: {issues}")

    def test_cmdi_shell_exec(self):
        issues = issues_of('''\
<?php
$f = $_GET['f'];
echo shell_exec("cat /etc/hosts " . $f);
''', "命令注入", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP shell_exec 命令注入漏报: {issues}")

    def test_deser_unserialize(self):
        issues = issues_of('''\
<?php
$data = unserialize($_COOKIE['session']);
''', "不安全反序列化", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP unserialize 漏报: {issues}")

    def test_weak_hash_md5(self):
        issues = issues_of('''\
<?php
if (md5($password) === $row['pass']) { echo "ok"; }
''', "弱加密算法", ".php")
        self.assertTrue(len(issues) >= 1, f"PHP md5 密码漏报: {issues}")

    def test_safe_prepare_no_fp(self):
        issues = issues_of('''\
<?php
$stmt = $conn->prepare("SELECT * FROM users WHERE id = ?");
$stmt->bind_param("i", $id);
$stmt->execute();
''', "SQL注入", ".php")
        self.assertEqual([], issues, f"PHP 参数化负例误报: {issues}")


class GoSecurityTest(unittest.TestCase):
    """Go 安全扫描（B1-P2）"""

    def test_cmdi_exec_command(self):
        issues = issues_of('''\
package main

import "os/exec"

func ping(ip string) {
    cmd := exec.Command("sh", "-c", "ping " + ip)
    out, _ := cmd.Output()
    _ = out
}
''', "命令注入", ".go")
        self.assertTrue(len(issues) >= 1, f"Go exec.Command 漏报: {issues}")

    def test_cmdi_command_context(self):
        issues = issues_of('''\
package main

import "os/exec"

func run(name string) {
    cmd := exec.CommandContext(ctx, "sh", "-c", "whoami; " + name)
    _ = cmd.Run()
}
''', "命令注入", ".go")
        self.assertTrue(len(issues) >= 1, f"Go CommandContext 漏报: {issues}")

    def test_cmdi_sh_c_var(self):
        """外部十轮验证发现的漏检形态：字面量 sh + 纯变量参数"""
        issues = issues_of('''\
package main

import "os/exec"

func ping(ip string) {
    out, _ := exec.Command("sh", "-c", ip).Output()
    _ = out
}
''', "命令注入", ".go")
        self.assertTrue(len(issues) >= 1, f"Go sh -c 纯变量漏报: {issues}")

    def test_cmdi_bin_sh_c_var(self):
        issues = issues_of('''\
package main

import "os/exec"

func run(target string) {
    _ = exec.Command("/bin/sh", "-c", target).Run()
}
''', "命令注入", ".go")
        self.assertTrue(len(issues) >= 1, f"Go /bin/sh -c 纯变量漏报: {issues}")

    def test_cmdi_ctx_no_fp(self):
        """ctx 是 context.Context 类型，不是注入源——不得误报"""
        issues = issues_of('''\
package main

import (
    "context"
    "os/exec"
)

func list(ctx context.Context) {
    _ = exec.CommandContext(ctx, "ls", "-l")
}
''', "命令注入", ".go")
        self.assertEqual([], issues, f"Go CommandContext(ctx, 常量) 误报: {issues}")

    def test_sqli_sprintf(self):
        issues = issues_of('''\
package main

import (
    "database/sql"
    "fmt"
)

func find(db *sql.DB, id string) {
    rows, _ := db.Query(fmt.Sprintf("SELECT * FROM users WHERE id = %s", id))
    _ = rows
}
''', "SQL注入", ".go")
        self.assertTrue(len(issues) >= 1, f"Go fmt.Sprintf SQLi 漏报: {issues}")

    def test_ssrf_http_get(self):
        issues = issues_of('''\
package main

import "net/http"

func fetch(url string) {
    resp, _ := http.Get(url)
    _ = resp
}
''', "SSRF", ".go")
        self.assertTrue(len(issues) >= 1, f"Go http.Get SSRF 漏报: {issues}")

    def test_safe_placeholder_no_fp(self):
        issues = issues_of('''\
package main

import "database/sql"

func find(db *sql.DB, id string) {
    rows, _ := db.Query("SELECT * FROM users WHERE id = ?", id)
    _ = rows
}
''', "SQL注入", ".go")
        self.assertEqual([], issues, f"Go 占位符负例误报: {issues}")


class RustSecurityTest(unittest.TestCase):
    """Rust 安全扫描（B1-P3）"""

    def test_cmdi_command_new(self):
        issues = issues_of('''\
use std::process::Command;

fn ping(ip: &str) {
    let out = Command::new("sh").arg("-c").arg(format!("ping {}", ip)).output();
    let _ = out;
}
''', "命令注入", ".rs")
        self.assertTrue(len(issues) >= 1, f"Rust Command::new 漏报: {issues}")

    def test_sqli_format(self):
        issues = issues_of('''\
fn find(conn: &Connection, id: &str) {
    let q = format!("SELECT * FROM users WHERE id = {}", id);
    let rows = conn.query_all(&q);
    let _ = rows;
}
''', "SQL注入", ".rs")
        self.assertTrue(len(issues) >= 1, f"Rust format! SQLi 漏报: {issues}")

    def test_safe_params_no_fp(self):
        issues = issues_of('''\
fn find(conn: &Connection, id: &str) {
    let rows = conn.query_all("SELECT * FROM users WHERE id = ?1", params![id]);
    let _ = rows;
}
''', "SQL注入", ".rs")
        self.assertEqual([], issues, f"Rust params! 负例误报: {issues}")


class KotlinSecurityTest(unittest.TestCase):
    """Kotlin 安全扫描（B1-P4）"""

    def test_cmdi_runtime_exec(self):
        issues = issues_of('''\
fun ping(path: String) {
    val p = Runtime.getRuntime().exec("ls " + path)
    p.waitFor()
}
''', "命令注入", ".kt")
        self.assertTrue(len(issues) >= 1, f"Kotlin Runtime.exec 漏报: {issues}")

    def test_cmdi_process_builder(self):
        issues = issues_of('''\
fun run(cmd: String) {
    ProcessBuilder("sh", "-c", "echo " + cmd).start()
}
''', "命令注入", ".kt")
        self.assertTrue(len(issues) >= 1, f"Kotlin ProcessBuilder 漏报: {issues}")

    def test_sqli_raw_query(self):
        issues = issues_of('''\
fun find(db: SQLiteDatabase, id: String) {
    val c = db.rawQuery("SELECT * FROM users WHERE id = " + id, null)
    c.use { }
}
''', "SQL注入", ".kt")
        self.assertTrue(len(issues) >= 1, f"Kotlin rawQuery 拼接漏报: {issues}")

    def test_sqlite_exec_sql_concat(self):
        issues = issues_of('''\
fun del(db: SQLiteDatabase, id: String) {
    db.execSQL("DELETE FROM users WHERE id = " + id)
}
''', "SQL注入", ".kt")
        self.assertTrue(len(issues) >= 1, f"Kotlin execSQL 拼接漏报: {issues}")

    def test_safe_selection_args_no_fp(self):
        issues = issues_of('''\
fun find(db: SQLiteDatabase, id: String) {
    val c = db.query("users", null, "id = ?", arrayOf(id), null, null, null)
    c.use { }
}
''', "SQL注入", ".kt")
        self.assertEqual([], issues, f"Kotlin selectionArgs 负例误报: {issues}")


if __name__ == "__main__":
    unittest.main()