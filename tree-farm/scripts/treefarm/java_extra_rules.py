# -*- coding: utf-8 -*-
"""Java 安全规则补充模块（2026-09-19 思维树四分支分析驱动新增）。
独立模块避免 analysis.py 超大文件再膨胀；由 detect_security_issues 对 .java 调用。
覆盖：SQL注入 / XSS / 路径遍历 / XXE / 反序列化 / SSRF / 弱加密 / 硬编码凭据。
"""
import re


def _scan_java_extra_security(lines, issues, severity_count, rel):
    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            continue

        # 1) SQL 注入：execute/executeQuery/executeUpdate/prepareStatement 拼接
        if re.search(r"(?:executeQuery|executeUpdate|prepareStatement|execute)\s*\([^)]*\+", line) \
                and re.search(r"(?i)(select|insert|update|delete|order\s+by|from)", line):
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "critical",
                           "desc": "SQL执行语句拼接动态内容：用户输入可注入SQL，建议 PreparedStatement 参数化",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 2) XSS：response 输出拼接 request 参数
        if re.search(r"(?:getWriter\(\)\.(?:write|print|println)|getOutputStream\(\)\.print)\s*\([^)]*request\.", line):
            issues.append({"file": rel, "line": i, "type": "XSS跨站脚本",
                           "severity": "high",
                           "desc": "HTTP响应直接输出用户可控内容且未转义：反射型XSS",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 3) 路径遍历：File 类文件操作拼接动态路径
        if re.search(r"(?:new\s+File|FileInputStream|FileReader|FileOutputStream|FileWriter)\s*\([^)]*\+", line):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "文件操作拼接动态路径：可能被 ../ 逃逸到任意目录",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 4) XXE：DocumentBuilderFactory/SAXParserFactory 默认加载外部实体
        if re.search(r"(DocumentBuilderFactory\.newInstance\(\)|SAXParserFactory\.newInstance\(\))", line):
            issues.append({"file": rel, "line": i, "type": "XXE外部实体注入",
                           "severity": "high",
                           "desc": "XML解析未禁用外部实体(DTD)：可读本地文件/SSRF，需 setFeature 禁用 external-general-entities",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 5) 反序列化：ObjectInputStream.readObject
        if re.search(r"(new\s+ObjectInputStream|readObject\s*\(\s*\))", line):
            issues.append({"file": rel, "line": i, "type": "不安全反序列化",
                           "severity": "critical",
                           "desc": "ObjectInputStream.readObject 反序列化不可信数据：可执行任意代码(ysoserial 类)",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 6) SSRF：URL/Socket/HttpURLConnection 动态目标
        if re.search(r"(new\s+URL\s*\(\s*[^\"')]|new\s+Socket\s*\(\s*[^\"')]|HttpURLConnection)", line):
            issues.append({"file": rel, "line": i, "type": "SSRF",
                           "severity": "high",
                           "desc": "网络请求目标为动态变量：用户可控URL可访问内网/本地",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 7) 弱加密：MD5/SHA-1/DES/RC4/AES-ECB
        if re.search(r"(?i)getInstance\s*\(\s*[\"'](MD5|SHA-1|DES|RC4|AES/ECB)", line):
            issues.append({"file": rel, "line": i, "type": "弱加密算法",
                           "severity": "medium",
                           "desc": "弱加密/弱哈希(MD5/SHA-1/DES/RC4/ECB)：可碰撞/破解，建议 SHA-256/AES-GCM",
                           "code": stripped[:100]})
            severity_count["medium"] += 1

        # 8) 硬编码凭据（r18：补 API_KEY/API_KEY 下划线大写变体——常量命名惯用法）
        if re.search(r"(?i)(password|passwd|apiKey|apikey|api_key|secretKey|secret_key|secret|accessKey|access_key|jwtSecret|jwt_secret|token)\s*=\s*[\"'][^\"']{6,}[\"']", line):
            issues.append({"file": rel, "line": i, "type": "硬编码凭据",
                           "severity": "high",
                           "desc": "硬编码密码/密钥字面量：泄露即全盘失守，应走环境变量/密钥管理",
                           "code": stripped[:100]})
            severity_count["high"] += 1


def _scan_java_extra_file_level(lines, issues, severity_count, rel):
    """Java 文件级弱信号（2026-09-19 新增）：
    先收集 getParameter 污点变量，再扫描输出/SQL 拼接，覆盖跨行场景。"""
    # 第一遍：收集污点变量（req.getParameter / request.getParameter 赋值）
    tainted = set()
    for i, line in enumerate(lines, 1):
        m = re.search(r"(\w+)\s*=\s*(?:req|request)\.getParameter\s*\(", line)
        if m:
            tainted.add(m.group(1))

    joined = "".join(lines)

    # 第二遍：逐行找 sink
    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            continue

        # 1) XSS：输出函数拼接污点变量或字符串拼接
        # r2：转义豁免——StringEscapeUtils.escapeHtml / escape() 转义后的拼接是安全写法
        out_pat = r"(?:getWriter\(\)\.(?:write|print|println)|getOutputStream\(\)\.print|out\.print(?:ln)?|response\.sendRedirect)\s*\([^)]*"
        has_plus = "+" in line
        hit_var = [v for v in tainted if re.search(r"\b" + re.escape(v) + r"\b", line)]
        escaped = bool(re.search(r"escapeHtml|escapeEcmaScript|escapeJavaScript|escape\s*\(", line))
        if re.search(out_pat, line) and (has_plus or hit_var) and not escaped:
            issues.append({"file": rel, "line": i, "type": "XSS跨站脚本",
                           "severity": "high",
                           "desc": "响应输出含用户可控内容（getParameter 变量或拼接）：反射型XSS（需人工确认）",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 2) SQL：prepareStatement/execute 拼接（含跨行场景：行内 + 或文件内 SQL 关键字拼接）
        if re.search(r"(prepareStatement|executeQuery|executeUpdate|Statement)", line) \
                and ("+" in line or "String.format" in line):
            if re.search(r"(?i)(select|insert|update|delete|order\s+by|from)", joined):
                issues.append({"file": rel, "line": i, "type": "SQL注入",
                               "severity": "high",
                               "desc": "SQL语句可能拼接动态内容（跨行/format）：建议参数化（需人工确认）",
                               "code": stripped[:100]})
                severity_count["high"] += 1

        # 3) String.format 直接 SQL
        if re.search(r"String\.format\s*\([^)]*(select|insert|update|delete|order\s+by)", line, re.IGNORECASE):
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "critical",
                           "desc": "String.format 构造 SQL：格式参数可注入",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 4) ScriptEngine 动态执行
        if re.search(r"(?:ScriptEngineManager|getEngineBy\w+|ScriptEngine)\s*\(", line) and "eval" in joined:
            issues.append({"file": rel, "line": i, "type": "动态执行",
                           "severity": "high",
                           "desc": "JS 脚本引擎执行代码：若代码来自外部输入可任意执行",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 5) Files.copy/Paths.get 路径变量（r2：补引号开头拼接形态 Paths.get("/tmp/" + name)）
        if re.search(r"Files\.(?:copy|move|newInputStream|newOutputStream|write)\s*\([^)]*Paths\.get\s*\([^\"')]", line) \
                or re.search(r"Files\.(?:copy|move|newInputStream|newOutputStream|write)\s*\([^)]*Paths\.get\s*\(\s*[\"'][^\"')]*[\"']\s*\+", line):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "Files 操作目标为 Paths.get(动态路径)：可 ../ 逃逸",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 6) FileInputStream/FileReader 变量（污点源或常见输入变量名 → 需人工确认）
        m = re.search(r"(?:new\s+FileInputStream|new\s+FileReader|ZipInputStream)\s*\(\s*(\w+)\s*\)", line)
        if m and (m.group(1) in tainted or m.group(1) in ("p", "path", "file", "name", "fn", "input")):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "文件读取目标为变量（可能来自用户输入）：建议白名单校验（需人工确认）",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 7) MyBatis ${} 字符串拼接（R2/50轮：@Select("...${name}") 是 SQL 拼接高危形态）
        if re.search(r"@(?:Select|Update|Insert|Delete)\s*\(\s*\"[^\"]*\$\{", line):
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "critical",
                           "desc": "MyBatis ${} 直接拼接：用户输入可注入 SQL，应改用 #{}(#\{\} 参数化)",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 8) StringBuilder 拼 SQL（跨行形态拆两条弱信号：初始化含SQL / append变量+文件含execute）
        if re.search(r"new\s+StringBuilder\s*\(\s*\"[^\"]*(?:select|from|insert|update|delete)\s", line, re.I):
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "medium",
                           "desc": "StringBuilder 初始化为 SQL 模板并拼接动态内容：可能被注入（需人工确认）",
                           "code": stripped[:100]})
            severity_count["medium"] += 1
        elif re.search(r"\.append\s*\(\s*[^\"'\s]", line) and re.search(r"execute\w*\s*\(", joined):
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "medium",
                           "desc": "StringBuilder.append 动态内容且文件含 SQL 执行：可能被注入（需人工确认）",
                           "code": stripped[:100]})
            severity_count["medium"] += 1

        # 9) FileChannel.open 路径变量
        if re.search(r"FileChannel\.open\s*\(\s*[^\"']", line):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "FileChannel.open 目标为变量路径：可 ../ 逃逸（需人工确认）",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 10) setAttribute 未转义 + 转发 JSP（跨文件 XSS 前哨；req 是常见缩写）
        if re.search(r"setAttribute\s*\(\s*\"[^\"]*\"\s*,\s*(?:request|req)\.", line):
            issues.append({"file": rel, "line": i, "type": "XSS跨站脚本",
                           "severity": "medium",
                           "desc": "setAttribute 存储未转义用户输入并转发 JSP：模板输出时可能触发反射型XSS（需人工确认）",
                           "code": stripped[:100]})
            severity_count["medium"] += 1

        # 11) WebView addJavascriptInterface（JS 桥接暴露给网页）
        if re.search(r"addJavascriptInterface\s*\(", line):
            issues.append({"file": rel, "line": i, "type": "不安全配置",
                           "severity": "high",
                           "desc": "WebView addJavascriptInterface 暴露 JS 桥：页面可调用 native 方法，需校验来源与 @JavascriptInterface 白名单",
                           "code": stripped[:100]})
            severity_count["high"] += 1
