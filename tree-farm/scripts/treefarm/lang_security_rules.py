# -*- coding: utf-8 -*-
"""多语言安全规则模块（2026-09-24 安全盲区终结战 · B1 新增）。
独立模块避免 analysis.py 继续膨胀；由 detect_security_issues 按扩展名调用。
覆盖四语言（全部走"泛化数据流"思路，非对题补丁）：
- PHP(P1)：SQLi / 命令注入 / 反序列化 / 弱哈希 / 动态 include
- Go(P2)  ：命令注入 / SQLi / SSRF / 硬编码密钥
- Rust(P3)：命令注入 / SQLi（format! 数据流）/ unsafe 提示
- Kotlin(P4)：命令注入 / SQLi / 动态加载
每条规则带负例豁免（参数化/占位符/白名单常量不报），防误报。
"""
import re


def _scan_php_security(lines, issues, severity_count, rel):
    """PHP 安全规则（结构型）"""
    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]  # 防 ReDoS：超长行截断（同 java_extra_rules）
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#", "/*", "*")):
            continue

        # 1) SQL 注入：三种形态（占位符 ? 参数化一律豁免）
        #    形态A：危险调用 + 变量直传/拼接（mysqli_query / ->query / ->exec / ->prepare）
        #    形态B：赋值拼接/插值字符串 + SQL 关键字（$sql = "SELECT ... " . $var / {$var}）
        sql_call = re.search(r"(?i)(mysqli_query\s*\(|->\s*(?:query|exec|prepare)\s*\(|\bquery\s*\()", stripped)
        assign_sql = re.search(r"(?i)\$[a-z_]\w*\s*=\s*[\"']", stripped)
        sql_kw = re.search(r"(?i)(select|insert|update|delete|order\s+by|from)", stripped)
        if "?" not in stripped and sql_kw:
            if sql_call and re.search(r"\$\w|\.\s*\$", stripped):
                issues.append({"file": rel, "line": i, "type": "SQL注入",
                               "severity": "critical",
                               "desc": "PHP SQL 动态拼接/变量直传：输入可注入，建议 PDO 预处理 + bindParam",
                               "code": stripped[:100]})
                severity_count["critical"] += 1
            elif assign_sql and re.search(r"\{\$\w|\.\s*\$|\$_", stripped):
                issues.append({"file": rel, "line": i, "type": "SQL注入",
                               "severity": "critical",
                               "desc": "PHP SQL 字符串插值/拼接（$var/{$var}/$_超全局）：输入可注入，建议参数化查询",
                               "code": stripped[:100]})
                severity_count["critical"] += 1

        # 2) 命令注入：命令执行函数 + 变量/拼接（常量命令不报）
        if re.search(r"(?i)\b(system|shell_exec|exec|passthru|proc_open|popen|pcntl_exec)\s*\(", stripped) \
                and (re.search(r"\$\w", stripped) or " . " in stripped or re.search(r"\.\s*\$\w", stripped)):
            issues.append({"file": rel, "line": i, "type": "命令注入",
                           "severity": "critical",
                           "desc": "PHP 命令执行拼接用户可控内容：可逃逸执行任意命令",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 3) 反序列化：unserialize( 直收超全局变量（$_GET/$_POST/$_COOKIE/$_REQUEST）
        if re.search(r"\bunserialize\s*\(\s*\$_(?:GET|POST|REQUEST|COOKIE|FILES)\[", stripped):
            issues.append({"file": rel, "line": i, "type": "不安全反序列化",
                           "severity": "critical",
                           "desc": "PHP unserialize 不可信输入：PHP 对象注入/任意代码执行（POP 链）",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 4) 弱哈希：md5/sha1 直接对变量做密码类运算
        if re.search(r"(?i)\b(md5|sha1)\s*\(\s*\$", stripped):
            issues.append({"file": rel, "line": i, "type": "弱加密算法",
                           "severity": "medium",
                           "desc": "PHP 弱哈希 md5/sha1：可碰撞/查表破解，密码存储建议 password_hash()",
                           "code": stripped[:100]})
            severity_count["medium"] += 1

        # 5) 动态 include/require：路径来自变量（本地文件包含 LFI）
        if re.search(r"(?i)\b(include|require)(_once)?\s*\(?\s*\$", stripped) \
                or re.search(r"(?i)\b(include|require)(_once)?\s+[^'\"]", stripped):
            issues.append({"file": rel, "line": i, "type": "路径遍历",
                           "severity": "high",
                           "desc": "PHP 动态 include/require 用户可控路径：本地文件包含(LFI)/远程包含(RFI)",
                           "code": stripped[:100]})
            severity_count["high"] += 1


def _scan_go_security(lines, issues, severity_count, rel):
    """Go 安全规则（结构型）"""
    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue

        # 1) 命令注入：exec.Command/CommandContext/os.StartProcess/syscall.Exec
        #    泛化（v4.9.22 按外部十轮验证修正）：提取调用参数 → 剥离 CommandContext 的
        #    context 首参（ctx 不是注入源，防误报）→ 剩余参数含变量即报——
        #    覆盖最常见漏检形态 exec.Command("sh","-c",v)/Command("/bin/sh","-c",v)
        m_cmd = re.search(r"(?i)(?:exec\.)?Command(?:Context)?\s*\(([^)]*)\)", stripped)
        m_proc = re.search(r"(?:os\.StartProcess|syscall\.Exec)\s*\(([^)]*)\)", stripped)
        if m_cmd or m_proc:
            args = (m_cmd or m_proc).group(1)
            if m_cmd and "CommandContext" in stripped:
                # context.Context 首参（ctx/context/c 惯用名）剥离——不是用户输入
                args = re.sub(r'^\s*(?:ctx|context|c)\s*,\s*', '', args)
            # 变量检测必须先剥掉字符串字面量（否则 "ls" 里的 ls 会被误当变量）
            args_nolit = re.sub(r'"[^"]*"|\'[^\']*\'|`[^`]*`', '', args)
            has_var = bool(re.search(r"\b[a-z_][a-z0-9_]*\b", args_nolit))
            if has_var:
                is_shell = bool(re.search(r"[\"'][^\"']*(?:sh|bash|cmd|zsh|powershell)[\"']\s*,\s*[\"']-c[\"']",
                                          args))
                issues.append({"file": rel, "line": i, "type": "命令注入",
                               "severity": "critical",
                               "desc": ("Go 命令执行含动态变量参数" + ("（shell 形态 sh -c 可注入任意命令）" if is_shell
                                        else "：参数化命令名/参数可注入 shell 元字符")),
                               "code": stripped[:100]})
                severity_count["critical"] += 1

        # 2) SQL 注入：Query/Exec 类 + fmt.Sprintf 或拼接；含 ? 占位符直传参数豁免
        if re.search(r"(?i)\.(QueryContext|QueryRow|Query|ExecContext|Exec)\s*\(", stripped) \
                and (re.search(r"fmt\.Sprintf", stripped) or " + " in stripped or "+" in stripped) \
                and re.search(r"(?i)(select|insert|update|delete|order\s+by)", stripped) \
                and "?" not in stripped:
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "critical",
                           "desc": "Go SQL 动态拼接：输入可注入，建议 database/sql 占位符 ? 参数化",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 3) SSRF：http.Get/Post/NewRequest/Client.Do 目标为变量（非字面量）
        if re.search(r"(?i)\b(http\.(Get|Post|PostForm|NewRequest)|(?:&http\.)?Client\.Do)\s*\(", stripped) \
                and re.search(r"(?:Get|Post|NewRequest|Do)\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*[,)]", stripped):
            issues.append({"file": rel, "line": i, "type": "SSRF",
                           "severity": "high",
                           "desc": "Go HTTP 请求目标为动态变量：用户可控 URL 可访问内网/云元数据",
                           "code": stripped[:100]})
            severity_count["high"] += 1

        # 4) 硬编码密钥：var 声明含 key/secret/token/password/credential = 字面量
        if re.search(r"(?i)^\s*(var|const)\s+[A-Za-z0-9_]*(?:api_?key|secret|token|password|passwd|credential)\w*\s*=\s*[\"']", stripped):
            issues.append({"file": rel, "line": i, "type": "硬编码凭据",
                           "severity": "medium",
                           "desc": "Go 源码内硬编码密钥/令牌：泄露即账密失守，应移入环境变量或密钥管理",
                           "code": stripped[:100]})
            severity_count["medium"] += 1


def _scan_rust_security(lines, issues, severity_count, rel):
    """Rust 安全规则（结构型）"""
    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*", "#")):
            continue

        # 1) 命令注入：Command::new + format!/变量拼接 arg
        if re.search(r"Command::new\s*\(", stripped) \
                and (re.search(r"format!", stripped)
                     or re.search(r"\.arg\s*\(\s*[a-z_][a-z0-9_]*", stripped)
                     or " + " in stripped):
            issues.append({"file": rel, "line": i, "type": "命令注入",
                           "severity": "critical",
                           "desc": "Rust 命令执行拼接动态内容：可注入 shell 元字符，建议 Command::args 参数化",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 2) SQL 注入：format! 生成含 SQL 关键字的字符串（数据流：format! → query）
        if re.search(r"format!\s*\(\s*[\"']", stripped) \
                and re.search(r"(?i)(select|insert|update|delete|order\s+by|from)", stripped) \
                and "?" not in stripped:
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "critical",
                           "desc": "Rust format! 动态拼 SQL：输入可注入，建议 rusqlite/sqlx 绑定参数 ?1/:name",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 3) unsafe 块提示（提示级，非漏洞判定）
        if stripped.startswith("unsafe"):
            issues.append({"file": rel, "line": i, "type": "不安全代码",
                           "severity": "low",
                           "desc": "Rust unsafe 块：需人工确认为内存安全操作（指针/FFI/可变静态）",
                           "code": stripped[:100]})
            severity_count["low"] += 1


def _scan_kt_security(lines, issues, severity_count, rel):
    """Kotlin 安全规则（结构型）"""
    for i, line in enumerate(lines, 1):
        if len(line) > 32768:
            line = line[:32768]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue

        # 1) 命令注入：Runtime.exec / ProcessBuilder + 拼接或变量
        if re.search(r"(?i)(Runtime\.getRuntime\(\)\.exec\s*\(|ProcessBuilder\s*\()", stripped) \
                and (" + " in stripped or "+" in stripped
                     or re.search(r"exec\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)", stripped)
                     or re.search(r"ProcessBuilder\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*[,)]", stripped)):
            issues.append({"file": rel, "line": i, "type": "命令注入",
                           "severity": "critical",
                           "desc": "Kotlin 命令执行拼接动态内容：可注入 shell 元字符，建议参数列表传递",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 2) SQL 注入：rawQuery/execSQL/query 拼接（selectionArgs 参数化豁免）
        if re.search(r"(?i)\.(rawQuery|execSQL|query|delete|update|insert)\s*\(", stripped) \
                and (" + " in stripped or "+" in stripped
                     or re.search(r"rawQuery\s*\(\s*\"", stripped)) \
                and re.search(r"(?i)(select|insert|update|delete|from|where)", stripped) \
                and "?" not in stripped:
            issues.append({"file": rel, "line": i, "type": "SQL注入",
                           "severity": "critical",
                           "desc": "Kotlin SQL 动态拼接：输入可注入，建议 rawQuery 占位符 ? + selectionArgs",
                           "code": stripped[:100]})
            severity_count["critical"] += 1

        # 3) 动态加载：System.load/loadLibrary 路径或库名来自变量（不报字面量）
        if re.search(r"(?i)\b(System\.load\s*\(|System\.loadLibrary\s*\()", stripped) \
                and re.search(r"load(?:Library)?\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*[,)]", stripped):
            issues.append({"file": rel, "line": i, "type": "不安全的动态加载",
                           "severity": "medium",
                           "desc": "Kotlin 动态加载 native 库路径来自变量：可能加载被篡改的库，应校验签名/固定路径",
                           "code": stripped[:100]})
            severity_count["medium"] += 1