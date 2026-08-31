"""
自动多输入生成器（v4.4新增）
自动构造边界输入、恶意payload、极端参数，用于沙箱动态分析
触发埋藏的休眠bug，覆盖更多代码分支
"""
import re
import string
from typing import List, Dict, Any, Tuple


class AutoInputGenerator:
    """自动多输入生成器"""

    def __init__(self, max_per_category: int = 10):
        self.max_per_category = max_per_category
        self._cache = {}

    def generate_all(self, func_name: str = "", param_hints: List[str] = None) -> List[Dict[str, Any]]:
        """生成所有类型的测试输入"""
        inputs = []
        inputs.extend(self.generate_boundary_inputs())
        inputs.extend(self.generate_malicious_payloads())
        inputs.extend(self.generate_extreme_params())
        if param_hints:
            inputs.extend(self.generate_typed_inputs(param_hints))
        return inputs[:self.max_per_category * 3]

    def generate_boundary_inputs(self) -> List[Dict[str, Any]]:
        """生成边界输入"""
        return [
            {"type": "boundary", "name": "empty_string", "value": "", "desc": "空字符串"},
            {"type": "boundary", "name": "single_space", "value": " ", "desc": "单个空格"},
            {"type": "boundary", "name": "whitespace_only", "value": " \t\n\r", "desc": "仅空白字符"},
            {"type": "boundary", "name": "zero", "value": 0, "desc": "零"},
            {"type": "boundary", "name": "negative_one", "value": -1, "desc": "负一"},
            {"type": "boundary", "name": "max_int", "value": 2**31 - 1, "desc": "32位最大整数"},
            {"type": "boundary", "name": "min_int", "value": -(2**31), "desc": "32位最小整数"},
            {"type": "boundary", "name": "none_value", "value": None, "desc": "None值"},
            {"type": "boundary", "name": "true", "value": True, "desc": "布尔真"},
            {"type": "boundary", "name": "false", "value": False, "desc": "布尔假"},
            {"type": "boundary", "name": "empty_list", "value": [], "desc": "空列表"},
            {"type": "boundary", "name": "empty_dict", "value": {}, "desc": "空字典"},
            {"type": "boundary", "name": "empty_tuple", "value": (), "desc": "空元组"},
            {"type": "boundary", "name": "special_chars", "value": string.punctuation, "desc": "所有标点符号"},
            {"type": "boundary", "name": "unicode_null", "value": "\x00", "desc": "Unicode空字符"},
            {"type": "boundary", "name": "very_long_string", "value": "A" * 10000, "desc": "超长字符串(10000字符)"},
        ]

    def generate_malicious_payloads(self) -> List[Dict[str, Any]]:
        """生成恶意payload"""
        return [
            {"type": "malicious", "name": "sql_injection_1", "value": "' OR '1'='1", "desc": "SQL注入-恒真"},
            {"type": "malicious", "name": "sql_injection_2", "value": "1; DROP TABLE users;--", "desc": "SQL注入-删表"},
            {"type": "malicious", "name": "sql_injection_3", "value": "' UNION SELECT * FROM passwords--", "desc": "SQL注入-联合查询"},
            {"type": "malicious", "name": "xss_1", "value": "<script>alert('XSS')</script>", "desc": "XSS-脚本注入"},
            {"type": "malicious", "name": "xss_2", "value": "<img src=x onerror=alert('XSS')>", "desc": "XSS-图片错误"},
            {"type": "malicious", "name": "xss_3", "value": "javascript:alert('XSS')", "desc": "XSS-javascript协议"},
            {"type": "malicious", "name": "command_injection_1", "value": "; cat /etc/passwd", "desc": "命令注入-分号"},
            {"type": "malicious", "name": "command_injection_2", "value": "| whoami", "desc": "命令注入-管道"},
            {"type": "malicious", "name": "command_injection_3", "value": "&& rm -rf /", "desc": "命令注入-与操作"},
            {"type": "malicious", "name": "path_traversal_1", "value": "../../../etc/passwd", "desc": "路径遍历-相对路径"},
            {"type": "malicious", "name": "path_traversal_2", "value": "/etc/passwd", "desc": "路径遍历-绝对路径"},
            {"type": "malicious", "name": "path_traversal_3", "value": "..\\..\\..\\windows\\system32", "desc": "路径遍历-Windows"},
            {"type": "malicious", "name": "serialization_payload", "value": "cos\nsystem\n(S'echo pwned'\ntR.", "desc": "反序列化payload-pickle"},
            {"type": "malicious", "name": "crlf_injection", "value": "admin\r\nSet-Cookie: session=hacked", "desc": "CRLF注入-HTTP头"},
            {"type": "malicious", "name": "ssrf", "value": "http://169.254.169.254/latest/meta-data/", "desc": "SSRF-云元数据"},
            {"type": "malicious", "name": "open_redirect", "value": "//evil.com", "desc": "开放重定向-协议相对URL"},
            {"type": "malicious", "name": "xml_external_entity", "value": "<!DOCTYPE foo [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]>", "desc": "XXE-外部实体"},
        ]

    def generate_extreme_params(self) -> List[Dict[str, Any]]:
        """生成极端参数"""
        return [
            {"type": "extreme", "name": "huge_list", "value": list(range(10000)), "desc": "超大列表(10000元素)"},
            {"type": "extreme", "name": "deeply_nested", "value": self._make_nested(50), "desc": "深层嵌套列表(50层)"},
            {"type": "extreme", "name": "float_max", "value": float('inf'), "desc": "浮点正无穷"},
            {"type": "extreme", "name": "float_min", "value": float('-inf'), "desc": "浮点负无穷"},
            {"type": "extreme", "name": "float_nan", "value": float('nan'), "desc": "浮点NaN"},
            {"type": "extreme", "name": "float_epsilon", "value": 1e-308, "desc": "浮点极小值"},
            {"type": "extreme", "name": "negative_huge", "value": -(10**100), "desc": "负超大整数"},
            {"type": "extreme", "name": "positive_huge", "value": 10**100, "desc": "正超大整数"},
            {"type": "extreme", "name": "complex_number", "value": 1+2j, "desc": "复数"},
            {"type": "extreme", "name": "bytes_data", "value": b'\x00\x01\x02\xff', "desc": "二进制数据"},
            {"type": "extreme", "name": "set_data", "value": {1, 2, 3}, "desc": "集合类型"},
            {"type": "extreme", "name": "frozenset_data", "value": frozenset([1, 2, 3]), "desc": "不可变集合"},
            {"type": "extreme", "name": "recursive_input", "value": self._make_recursive(), "desc": "递归引用对象"},
            {"type": "extreme", "name": "emoji_string", "value": "😀🎉🔥💻📱🌍🎵🎮", "desc": "emoji字符串"},
            {"type": "extreme", "name": "multibyte_string", "value": "中文日本語한국어العربية", "desc": "多字节字符串"},
            {"type": "extreme", "name": "control_chars", "value": "\x01\x02\x03\x1b\x7f", "desc": "控制字符"},
        ]

    def generate_typed_inputs(self, param_hints: List[str]) -> List[Dict[str, Any]]:
        """根据参数类型提示生成针对性输入"""
        inputs = []
        for hint in param_hints:
            hint_lower = hint.lower() if hint else ""
            if any(k in hint_lower for k in ["path", "file", "filename", "dir"]):
                inputs.extend([
                    {"type": "typed", "name": f"path_traversal_{hint}", "value": "../../../etc/passwd", "desc": f"路径遍历针对{hint}"},
                    {"type": "typed", "name": f"absolute_path_{hint}", "value": "/etc/passwd", "desc": f"绝对路径针对{hint}"},
                ])
            elif any(k in hint_lower for k in ["sql", "query", "id", "user_id", "name"]):
                inputs.extend([
                    {"type": "typed", "name": f"sql_injection_{hint}", "value": "' OR '1'='1", "desc": f"SQL注入针对{hint}"},
                ])
            elif any(k in hint_lower for k in ["cmd", "command", "host", "ip"]):
                inputs.extend([
                    {"type": "typed", "name": f"command_injection_{hint}", "value": "; whoami", "desc": f"命令注入针对{hint}"},
                ])
            elif any(k in hint_lower for k in ["url", "link", "href", "redirect"]):
                inputs.extend([
                    {"type": "typed", "name": f"ssrf_{hint}", "value": "http://127.0.0.1:22", "desc": f"SSRF针对{hint}"},
                    {"type": "typed", "name": f"open_redirect_{hint}", "value": "//evil.com", "desc": f"开放重定向针对{hint}"},
                ])
        return inputs

    def _make_nested(self, depth: int) -> List:
        """生成深层嵌套列表"""
        result = []
        current = result
        for i in range(depth):
            current.append([])
            current = current[0]
        return result

    def _make_recursive(self) -> List:
        """生成递归引用对象"""
        result = [1, 2, 3]
        result.append(result)
        return result

    def extract_param_hints(self, func_source: str) -> List[str]:
        """从函数源码中提取参数名作为类型提示"""
        hints = []
        # 匹配函数定义中的参数
        match = re.search(r'def\s+\w+\s*\(([^)]*)\)', func_source)
        if match:
            params = match.group(1)
            for param in params.split(','):
                param = param.strip()
                if param and param != 'self' and param != 'cls':
                    # 去除默认值和类型注解
                    param_name = re.split(r'[:=]', param)[0].strip()
                    if param_name:
                        hints.append(param_name)
        return hints

    def generate_report(self, func_name: str, inputs: List[Dict], results: List[Dict]) -> Dict[str, Any]:
        """生成输入生成和测试报告"""
        total = len(inputs)
        crashes = sum(1 for r in results if r.get('crash'))
        exceptions = sum(1 for r in results if r.get('exception'))
        assertions = sum(1 for r in results if r.get('assertion_failure'))
        return {
            "func_name": func_name,
            "total_inputs": total,
            "crashes": crashes,
            "exceptions": exceptions,
            "assertion_failures": assertions,
            "coverage_rate": f"{sum(1 for r in results if r.get('executed'))}/{total}",
            "input_categories": list(set(i['type'] for i in inputs)),
            "findings": [r for r in results if r.get('crash') or r.get('exception') or r.get('assertion_failure')],
        }
