# -*- coding: utf-8 -*-
"""
树场 50 轮测试 · R4：JS/TS 前端安全 II（unittest）

运行：
  python3 -m unittest tests.test_round23_js2 -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treefarm.analysis import detect_security_issues  # noqa: E402


def make_file(content: str, suffix=".js") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="tf_r23_")
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


class JsCommandTest(unittest.TestCase):
    """JS 命令注入 · 变体"""

    def test_template_cmd(self):
        issues = issues_of('''\
const { exec } = require("child_process");
function run(name) {
    exec(`mkdir -p /tmp/${name}`);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"模板串 exec 漏报: {issues}")

    def test_fork_silent(self):
        issues = issues_of('''\
const cp = require("child_process");
function start(cmd) {
    cp.execFile("node", ["-e", cmd]);
}
''', "命令注入")
        self.assertTrue(len(issues) >= 1, f"execFile -e 漏报: {issues}")

    def test_safe_exec_file_args(self):
        # 安全：execFile 参数数组无 shell → 可接受（无注入）
        issues = issues_of('''\
const cp = require("child_process");
function ls(dir) {
    return cp.execFile("ls", ["-la", dir]);
}
''', "命令注入")
        self.assertEqual([], issues, f"execFile 数组误报: {issues}")


class JsSsrfTest(unittest.TestCase):
    """JS SSRF · 变体"""

    def test_http_get_dynamic(self):
        issues = issues_of('''\
const http = require("http");
function fetch(u) {
    http.get(u, (res) => {});
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"http.get 变量漏报: {issues}")

    def test_axios_template(self):
        issues = issues_of('''\
const axios = require("axios");
function api(id) {
    return axios.get(`http://10.0.0.1/data/${id}`);
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"axios 模板漏报: {issues}")

    def test_got_dynamic(self):
        issues = issues_of('''\
const got = require("got");
function browse(url) {
    return got(url);
}
''', "SSRF")
        self.assertTrue(len(issues) >= 1, f"got 变量漏报: {issues}")


class JsPrototypeTest(unittest.TestCase):
    """原型污染 · 变体"""

    def test_recursive_merge(self):
        issues = issues_of('''\
function merge(target, source) {
    for (const key of Object.keys(source)) {
        if (typeof source[key] === "object") {
            merge(target[key], source[key]);
        } else {
            target[key] = source[key];
        }
    }
}
function vuln(data) {
    return merge({}, JSON.parse(data));
}
''', "原型污染")
        self.assertTrue(len(issues) >= 1, f"递归 merge+parse 漏报: {issues}")

    def test_deep_copy_vuln(self):
        issues = issues_of('''\
function clone(obj) {
    if (typeof obj !== "object") return obj;
    const out = Array.isArray(obj) ? [] : {};
    for (const k in obj) {
        out[k] = clone(obj[k]);
    }
    return out;
}
function handle(input) {
    return clone(JSON.parse(input));
}
''', "原型污染")
        self.assertTrue(len(issues) >= 1, f"deep clone 污染漏报: {issues}")


class JsXssTest(unittest.TestCase):
    """JS XSS · 变体"""

    def test_vue_vhtml(self):
        issues = issues_of('''\
Vue.component("post", {
    template: "<div v-html='content'></div>",
});
''', "XSS跨站脚本", ".vue")
        self.assertTrue(len(issues) >= 1, f"v-html 漏报: {issues}")

    def test_document_write(self):
        issues = issues_of('''\
function render(param) {
    document.write("<p>" + param + "</p>");
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"document.write 漏报: {issues}")

    def test_insert_adjacent(self):
        issues = issues_of('''\
function show(html) {
    document.getElementById("box").insertAdjacentHTML("beforeend", html);
}
''', "XSS跨站脚本")
        self.assertTrue(len(issues) >= 1, f"insertAdjacentHTML 漏报: {issues}")


class JsDynamicTest(unittest.TestCase):
    """JS 动态执行 · 变体"""

    def test_constructor(self):
        issues = issues_of('''\
function compile(code) {
    return (0, eval)("(" + code + ")");
}
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"间接 eval 漏报: {issues}")

    def test_require_dynamic(self):
        issues = issues_of('''\
function load(mod) {
    return require(mod);
}
''', "动态执行")
        self.assertTrue(len(issues) >= 1, f"require 动态漏报: {issues}")


class JsFpGuardTest(unittest.TestCase):
    """JS 误报治理"""

    def test_text_content_safe(self):
        issues = issues_of('''\
function show(name) {
    document.getElementById("out").textContent = name;
}
''', "XSS跨站脚本")
        self.assertEqual([], issues, f"textContent 误报: {issues}")

    def test_template_literal_safe(self):
        issues = issues_of('''\
const url = `https://api.example.com/v1/items`;
async function getItems() {
    return await fetch(url);
}
''', "SSRF")
        self.assertEqual([], issues, f"固定模板 URL 误报: {issues}")

    def test_child_process_const(self):
        issues = issues_of('''\
const cp = require("child_process");
function ping() {
    cp.exec("ping -c 1 127.0.0.1");
}
''', "命令注入")
        self.assertEqual([], issues, f"纯字面量 exec 误报: {issues}")


if __name__ == "__main__":
    unittest.main()