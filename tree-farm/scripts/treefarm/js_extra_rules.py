# -*- coding: utf-8 -*-
"""JavaScript/TypeScript 安全规则补充模块（2026-09-19 思维树四分支分析驱动新增）。
覆盖：命令注入 / 路径遍历 / SSRF / 原型污染 / ReDoS / 动态执行 / XSS(jQuery)。
行级 + 文件级（污点变量收集），与 Java 补充模块同架构（统一语言补充规则模式）。
"""
import re


def _scan_js_extra_security(lines, issues, severity_count, rel):
    # r4：文件级 const 字面量/无占位模板集合（const url = `https://...` → 固定目标，SSRF 豁免）
    const_literals = set()
    for _ln in lines:
        m = re.search(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(`[^`]*`|\"[^\"]*\"|'[^']*')", _ln)
        if m and "${" not in m.group(2):
            const_literals.add(m.group(1))

    # P5：编码函数赋值变量集合（String.fromCharCode/atob 拼接 → 危险调用 = 编码混淆绕过）
    encoded_vars = set()
    for _ln in lines:
        m = re.search(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:String\.fromCharCode|atob|Buffer\.from)\s*\(", _ln)
        if m:
            encoded_vars.add(m.group(1))

    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            continue

        # 1) 命令注入：child_process exec/execSync/spawn 拼接或模板串
        # r4：execFile 带 -e/-c 参数（执行代码等价 eval）
        # P5：编码函数赋值变量（fromCharCode/atob/Buffer.from → 编码混淆命令）
        if re.search(r"(?:exec|execSync)\s*\([^)]*\+", line) \
                or re.search(r"exec(Sync)?\s*\(`[^`]*\$\{", line) \
                or re.search(r"spawn\s*\(\s*['\"`]sh['\"`]\s*,\s*['\"`]-c['\"`]\s*,\s*\w", line) \
                or re.search(r"spawn\s*\(\s*['\"]sh['\"]\s*,\s*\[\s*['\"]-c['\"]\s*,\s*\w", line) \
                or re.search(r"child_process\s*\.\s*exec\s*\([^)]*\+", line) \
                or re.search(r"execFile\s*\([^)]*[\"']-[ec][\"']", line) \
                or any(re.search(r"(?:exec|execSync|spawn|fork|execFile)\s*\([^)]*\b" + re.escape(v) + r"\b", line)
                       for v in encoded_vars):
            issues.append({"file": rel, "line": i, "type": "命令注入",
                           "severity": "critical",
                           "desc": "child_process 命令执行（含编码混淆变量 String.fromCharCode/atob）：用户输入可注入 shell 命令",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 2) 路径遍历：fs 文件操作拼接动态路径
        if re.search(r"fs\.(readFile|readFileSync|writeFile|writeFileSync|unlink|unlinkSync|createReadStream|createWriteStream|copyFileSync)\s*\([^)]*\+", line):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "文件操作拼接动态路径：可能被 ../ 逃逸到任意目录",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 3) SSRF：fetch/http/axios/net/got 动态目标
        # r4：const 字面量/无占位模板变量豁免（const url = \`https://...\` 是固定目标）
        if re.search(r"(?:fetch|axios\.(?:get|post|put|delete))\s*\(\s*[^\"'`\[]", line) \
                or re.search(r"http\.(?:get|request)\s*\(\s*[^\"'`\[]", line) \
                or re.search(r"http\.(?:get|request)\s*\([^)]*\+", line) \
                or re.search(r"(?:fetch|axios\.(?:get|post))\s*\(`[^`]*\$\{", line) \
                or re.search(r"\bgot\s*\(\s*[^\"'`\[]", line) \
                or re.search(r"net\.createConnection\s*\([^)]*,[^)]*\w", line):
            # 变量仅在 const 字面量/无占位模板时豁免（固定 URL 不是 SSRF）
            m_dyn = re.search(r"(?:fetch|axios\.(?:get|post|put|delete)|http\.(?:get|request)|got)\s*\(\s*(\w+)", line)
            if not (m_dyn and m_dyn.group(1) in const_literals):
                issues.append({"file": rel, "line": i, "type": "SSRF",
                               "severity": "high",
                               "desc": "网络请求目标为动态变量：用户可控URL可访问内网/本地",
                               "code": stripped[:100]})
                severity_count["high"] += 1

        # 4) 原型污染（r3：__proto__.admin = 形态 —— __proto__ 后跟子属性赋值同样污染原型）
        if re.search(r"Object\.assign\s*\([^)]*JSON\.parse", line) \
                or re.search(r"\.__proto__\s*=", line) \
                or re.search(r"\.__proto__\.[\w$]+\s*=", line) \
                or re.search(r"merge\s*\(\s*[^)]*JSON\.parse", line) \
                or re.search(r"clone\s*\(\s*[^)]*JSON\.parse", line) \
                or re.search(r"\{\s*\.\.\.\w+\s*\}", line) \
                or re.search(r"\[\s*\w+\s*\]\s*=\s*(?:req\.|input\s*\()", line):
            issues.append({"file": rel, "line": i, "type": "原型污染",
                           "severity": "high",
                           "desc": "对象合并/展开/__proto__写入不可信数据：原型污染可致 RCE/绕过",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 5) ReDoS：嵌套量词危险正则
        if re.search(r"/\^\([\[\]\w\s\.\\-]+[+*]\)[+*]", line) \
                or re.search(r"\([\w\\]+[+*]\)[+*][^/]*/\.(test|match|exec|replace)", line) \
                or re.search(r"\.(?:test|match|exec|replace)\s*\(\s*/(?:\[[\w\s.\-]*\]|[\w\s.\-]+)[+*]\)[+*]", line) \
                or re.search(r"new\s+RegExp\s*\(\s*['\"`]\^\([^)]*\)[+*]", line):
            issues.append({"file": rel, "line": i, "type": "正则ReDoS",
                           "severity": "medium",
                           "desc": "嵌套量词正则（如 (a+)+$）：可灾难回溯卡死进程（ReDoS）",
                           "code": stripped[:100]})
            severity_count["medium"] += 1

        # 6) 动态执行（r4：间接 eval 形态 (0, eval)(code)）
        if re.search(r"new\s+Function\s*\([^)]*\w", line) \
                or re.search(r"require\s*\(\s*[^\"'`\[]", line) \
                or re.search(r"vm\.runInNewContext\s*\([^)]*\w", line) \
                or re.search(r"\(\s*0\s*,\s*eval\s*\)\s*\(", line):
            issues.append({"file": rel, "line": i, "type": "动态执行",
                           "severity": "high",
                           "desc": "动态代码执行/加载（Function/require/vm）：外部输入可任意执行",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 7) XSS：jQuery .html(变量)
        if re.search(r"\.html\s*\(\s*[^\"'`\[]", line):
            issues.append({"file": rel, "line": i, "type": "XSS跨站脚本",
                           "severity": "high",
                           "desc": "jQuery .html() 写入动态内容：反射型/存储型XSS",
                           "code": stripped[:100]})
            severity_count["high"] += 1


def _scan_js_extra_file_level(lines, issues, severity_count, rel):
    """JS 文件级：收集用户输入变量（input/req.query/req.body/params），
    检查 fs/http sink 使用污点变量。r3：补「拼接含用户源」形态
    （const cmd = "rm -rf " + req.query.path → cmd 也是污点）。"""
    tainted = set()
    for line in lines:
        for m in re.finditer(r"\b(\w+)\s*=\s*(?:req\.query|req\.body|req\.params|params|input)\s*[;.\[]", line):
            tainted.add(m.group(1))
        # r3：拼接形态——右值含用户源（"前缀" + req.xxx / `模板${req.xxx}` / 直接 req.xxx.path）
        for m in re.finditer(r"\b(\w+)\s*=\s*[^;\n]{0,120}?(?:req\.(?:query|body|params|files)|params\.)\s*\w*\s*[+\};]?", line):
            tainted.add(m.group(1))
    if not tainted:
        return
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        hit = [v for v in tainted if re.search(r"\b" + re.escape(v) + r"\b", line)]
        # fs 操作使用污点变量（无拼接时；r3：unlinkSync/writeFileSync 等家族补全）
        if hit and re.search(r"fs\.(readFile|readFileSync|writeFile|writeFileSync|unlink|unlinkSync|createReadStream|createWriteStream|copyFileSync|appendFile|appendFileSync)\s*\(\s*\w", line):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "文件操作使用用户输入变量（跨行污点）：建议白名单（需人工确认）",
                           "code": stripped[:100]})
            severity_count["high"] += 1
        # http/fetch 使用污点变量
        if hit and re.search(r"(?:fetch|http\.(?:get|request)|net\.createConnection)\s*\([^)]*\w", line):
            issues.append({"file": rel, "line": i, "type": "SSRF",
                           "severity": "high",
                           "desc": "网络请求使用用户输入变量（跨行污点）：可访问内网（需人工确认）",
                           "code": stripped[:100]})
            severity_count["high"] += 1
        # r3：命令执行使用污点变量（跨行：exec(cmd) / execSync(拼好的串)）
        if hit and re.search(r"\b(?:exec|execSync|spawn)\s*\(\s*\w", line):
            issues.append({"file": rel, "line": i, "type": "命令注入",
                           "severity": "critical",
                           "desc": "命令执行使用用户输入变量（跨行污点）：可注入 shell 命令（需人工确认）",
                           "code": stripped[:100]})
            severity_count["critical"] += 1
