# -*- coding: utf-8 -*-
"""
树场 20 轮测试 · 第 3 轮：JS/TS 前端安全地狱（unittest，零依赖）

目标：测 JS 补充规则（js_extra_rules.py + analysis.py JS/HTML 分支）的短板。
用例来源（权威）：
  - OWASP Juice Shop 风格漏洞模式（原型污染/ReDoS/XSS）
  - Node.js 安全实践（命令注入/路径遍历/SSRF）
  - 混淆对抗：模板串、跨行污点、转义变体

运行：
  python3 -m unittest tests.test_round3_js_security -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".js") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="treefarm_r3_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def issues_of(content, itype=None, suffix=".js"):
    path = make_file(content, suffix)
    try:
        all_issues = detect_security_issues([path])["issues"]
        if itype:
            return [i for i in all_issues if i["type"] == itype]
        return all_issues
    finally:
        os.unlink(path)


class JsCmdInjectionTest(unittest.TestCase):
    """JS 命令注入（CWE-78）"""

    def test_exec_concat(self):
        issues = issues_of('''\
const { exec } = require("child_process");
function run(cmd) {
    exec("ls -la " + cmd);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"exec 拼接漏报: {issues}")

    def test_exec_template(self):
        issues = issues_of('''\
const { execSync } = require("child_process");
function run(userInput) {
    execSync(`ping -c 1 ${userInput}`);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"execSync 模板串漏报: {issues}")

    def test_spawn_sh_c(self):
        issues = issues_of('''\
const { spawn } = require("child_process");
function run(cmd) {
    spawn("sh", ["-c", cmd]);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"spawn sh -c 漏报: {issues}")

    def test_cross_line_cmd(self):
        # 跨行：user input 先赋值再拼
        issues = issues_of('''\
const { exec } = require("child_process");
const cmd = "rm -rf " + req.query.path;
exec(cmd);
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"跨行命令注入漏报: {issues}")


class JsPathTraversalTest(unittest.TestCase):
    """JS 路径遍历（CWE-22）"""

    def test_read_file_concat(self):
        issues = issues_of('''\
const fs = require("fs");
function read(fname) {
    return fs.readFileSync("/var/data/" + fname);
}
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"readFileSync 拼接漏报: {issues}")

    def test_cross_line_fs(self):
        issues = issues_of('''\
const fs = require("fs");
const f = req.query.file;
fs.unlinkSync(f);
''', "路径遍历")
        self.assertTrue(len(issues) >= 1, f"跨行 fs 漏报: {issues}")

    def test_safe_const_path(self):
        # 安全：固定路径 → 不报
        issues = issues_of('''\
const fs = require("fs");
const data = fs.readFileSync("/etc/hostname");
''', "路径遍历")
        self.assertEqual([], issues, f"固定路径误报: {issues}")


class JsSsrfTest(unittest.TestCase):
    """JS SSRF（CWE-918）"""

    def test_fetch_var(self):
        issues = issues_of('''\
async function get(url) {
    return await fetch(url);
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"fetch(变量) 漏报: {issues}")

    def test_fetch_template(self):
        issues = issues_of('''\
async function api(id) {
    return await fetch(`http://internal/api/${id}`);
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"fetch 模板串漏报: {issues}")

    def test_axios_concat(self):
        issues = issues_of('''\
const axios = require("axios");
function hit(base) {
    return axios.get(base + "/admin");
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"axios 拼接漏报: {issues}")

    def test_safe_fetch_const(self):
        # 安全：固定 URL → 不报
        issues = issues_of('''\
async function ping() {
    return await fetch("https://api.github.com/zen");
}
''', "SSRF")
        self.assertEqual([], issues, f"固定 URL 误报: {issues}")


class JsPrototypePollutionTest(unittest.TestCase):
    """原型污染（CWE-1321）"""

    def test_object_assign_parse(self):
        issues = issues_of('''\
function merge(a, b) {
    return Object.assign({}, a, JSON.parse(b));
}
''', "原型污染")
        self.assertTrue(len(issues) >= 1, f"Object.assign+JSON.parse 漏报: {issues}")

    def test_proto_assign(self):
        issues = issues_of('''\
function vuln(obj) {
    obj.__proto__.admin = true;
}
''', "原型污染")
        self.assertTrue(len(issues) >= 1, f"__proto__ 赋值漏报: {issues}")

    def test_merge_json_parse(self):
        issues = issues_of('''\
function deepMerge(target, src) {
    return merge(target, JSON.parse(src));
}
''', "原型污染")
        self.assertTrue(len(issues) >= 1, f"merge+JSON.parse 漏报: {issues}")


class JsRedosTest(unittest.TestCase):
    """JS ReDoS（CWE-1333）"""

    def test_nested_group(self):
        issues = issues_of('''\
function check(s) {
    return /^([a-z]+)+$/.test(s);
}
''', "正则ReDoS")
        self.assertTrue(len(issues) >= 1, f"嵌套量词漏报: {issues}")

    def test_new_regexp_nested(self):
        issues = issues_of('''\
function check(s) {
    return new RegExp("^(\\\\d+)+$").test(s);
}
''', "正则ReDoS")
        self.assertTrue(len(issues) >= 1, f"new RegExp 嵌套漏报: {issues}")

    def test_safe_regex(self):
        issues = issues_of('''\
function check(s) {
    return /^[a-z0-9_]+$/.test(s);
}
''', "正则ReDoS")
        self.assertEqual([], issues, f"安全正则误报: {issues}")


class JsDynamicExecTest(unittest.TestCase):
    """JS 动态执行（CWE-94）"""

    def test_new_function(self):
        issues = issues_of('''\
function compile(code) {
    return new Function(code);
}
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"new Function 漏报: {issues}")

    def test_vm_run(self):
        issues = issues_of('''\
const vm = require("vm");
function sandbox(code) {
    return vm.runInNewContext(code);
}
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"vm.runInNewContext 漏报: {issues}")


class JsXssTest(unittest.TestCase):
    """JS XSS（CWE-79）"""

    def test_jquery_html_dynamic(self):
        issues = issues_of('''\
function render(msg) {
    $("#out").html(msg);
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"jQuery .html 漏报: {issues}")

    def test_inner_html(self):
        issues = issues_of('''\
function show(name) {
    document.body.innerHTML = "<p>" + name + "</p>";
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"innerHTML 拼接漏报: {issues}")

    def test_safe_text_content(self):
        # 安全：textContent → 不报
        issues = issues_of('''\
function show(name) {
    document.getElementById("out").textContent = name;
}
''', "XSS跨站脚本")
        self.assertEqual([], issues, f"textContent 误报: {issues}")


class JsFalsePositiveTest(unittest.TestCase):
    """JS 误报治理"""

    def test_string_literal_exec(self):
        # 字符串字面量里的 exec 字样 → 不报
        issues = issues_of('''\
const DOC = "Never use exec(userInput) directly in production";
''', "命令注入")
        self.assertEqual([], issues, f"字符串字样误报: {issues}")

    def test_comment_exec(self):
        issues = issues_of('''\
// TODO: review this exec() usage later
const x = 1;
''', "命令注入")
        self.assertEqual([], issues, f"注释误报: {issues}")


class HtmlInlineJsTest(unittest.TestCase):
    """HTML 内联 JS 参与检测（v4.9.11 能力验证）"""

    def test_html_inline_eval(self):
        issues = issues_of('''\
<!DOCTYPE html>
<html>
<body>
<script>
function go() {
    eval(location.hash.slice(1));
}
</script>
</body>
</html>
''', "动态执行", ".html")
        self.assertTrue(len(issues) >= 1, f"HTML 内联 eval 漏报: {issues}")


if __name__ == "__main__":
    unittest.main()
