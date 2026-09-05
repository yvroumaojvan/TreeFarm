# -*- coding: utf-8 -*-
"""树场机制 —— 配置与 LLM 客户端（v4.7；v3.6 拆分自单文件 tree_farm.py）。

包含：极简 TOML 解析 / 配置文件定位（.treefarm.toml / .treefarm.json）/
LLMClient（OpenAI 兼容接口，纯标准库 urllib，自动识别用户的 API key）。
"""

import json
import os
from typing import Any, Dict, List, Optional

from .common import LLM_TIMEOUT


# ========== LLM 客户端（OpenAI 兼容，纯标准库，自动识别用户的 API） ==========
PROVIDERS = [
    # (环境变量, base_url, 默认模型) —— TREEFARM_* 最强，其次各家通用 key
    ("TREEFARM_API_KEY", None, None),
    ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("ARK_API_KEY", "https://ark.cn-beijing.volces.com/api/v3", "doubao-pro-32k"),
]


def _parse_toml(text: str) -> Dict[str, Any]:
    """极简 TOML 解析（v3.2 新增，零依赖，够用即可）：
    支持 [section] 表、key = "字符串" / '字符串' / 数字 / true|false / [数组]。
    不支持嵌套表/日期等高级语法（.treefarm.toml 用不到）。"""
    result: Dict[str, Any] = {}
    section = result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            section = result.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip('"').strip("'")
                     for v in val[1:-1].split(",") if v.strip()]
            section[key] = items
        elif val.startswith('"') and val.endswith('"'):
            section[key] = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            section[key] = val[1:-1]
        elif val.lower() in ("true", "false"):
            section[key] = val.lower() == "true"
        else:
            try:
                section[key] = int(val)
            except ValueError:
                try:
                    section[key] = float(val)
                except ValueError:
                    section[key] = val
    return result


def _load_config_data(path: str) -> Optional[Dict[str, Any]]:
    """按扩展名读取配置文件（.json / .toml），失败返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if path.endswith(".toml"):
            data = _parse_toml(text)
        else:
            data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_config(root: Optional[str] = None) -> Dict[str, Any]:
    """读取配置文件（v3.2：支持 .treefarm.json 与 .treefarm.toml）。
    查找顺序（先到先得，不合并）：<root>/.treefarm.toml → <root>/.treefarm.json
    → 当前目录 → ~/.treefarm.toml → ~/.treefarm.json。
    找不到返回 {}。"""
    names = [".treefarm.toml", ".treefarm.json"]
    bases = []
    if root:
        bases.append(root)
    bases.append(".")
    bases.append(os.path.expanduser("~"))
    for base in bases:
        for name in names:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                data = _load_config_data(path)
                if data is not None:
                    return data
    return {}


class LLMClient:
    """引擎的「电话」：自动找卡插上，把问题打包发给大模型，拿回 JSON 回答。零依赖 urllib。"""

    def __init__(self, root: Optional[str] = None):
        self.api_key = ""
        self.base_url = ""
        self.model = ""
        self.source = "未找到"
        self.detect(root)

    def detect(self, root: Optional[str] = None) -> None:
        cfg = load_config(root)
        if cfg.get("api_key"):
            self.api_key = cfg.get("api_key", "")
            self.base_url = cfg.get("base_url", "https://api.deepseek.com/v1")
            self.model = cfg.get("model", "deepseek-chat")
            self.source = "配置文件（.treefarm.toml / .treefarm.json）"
            return
        for env, url, model in PROVIDERS:
            key = os.environ.get(env)
            if key:
                self.api_key = key
                if env == "TREEFARM_API_KEY":
                    self.base_url = os.environ.get("TREEFARM_API_URL", "https://api.deepseek.com/v1")
                    self.model = os.environ.get("TREEFARM_MODEL", "deepseek-chat")
                else:
                    self.base_url = url
                    self.model = model
                self.source = f"环境变量 {env}"
                return
        self.source = "未找到可用 API key（dry-run 模式）"

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: List[Dict[str, str]], timeout: Optional[int] = None) -> str:
        import urllib.error
        import urllib.request
        url = self.base_url.rstrip("/") + "/chat/completions"
        t = timeout or LLM_TIMEOUT
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }

        def _do(payload: bytes) -> Dict[str, Any]:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=t) as resp:
                return json.loads(resp.read().decode("utf-8"))

        def _body(with_format: bool) -> bytes:
            body = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
            }
            if with_format:
                body["response_format"] = {"type": "json_object"}
            return json.dumps(body).encode("utf-8")

        try:
            data = _do(_body(True))
        except Exception:
            # 不支持 response_format 的兼容端点可能直接 4xx、也可能卡到超时（如商汤托管）
            # → 去掉该参数重试一次，仍失败才报错
            try:
                data = _do(_body(False))
            except Exception as e2:
                raise RuntimeError(
                    f"请求失败（网络不通 或 key 无效）: {e2}\n"
                    "国内网络建议换 DeepSeek/通义：在项目目录写 .treefarm.json 指定\n"
                    "（格式: {\"api_key\":\"sk-...\",\"base_url\":\"https://api.deepseek.com/v1\","
                    "\"model\":\"deepseek-chat\"}）")
        return data["choices"][0]["message"]["content"]
