"""
llm_client.py - 统一 LLM 调用接口

默认 provider: minimax/auto（OpenClaw 平台内网代理）
支持切换: deepseek / openai / 其他
"""

from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Optional


class LLMClient:
    """统一 LLM 客户端"""

    def __init__(
        self,
        provider: str = "minimax",
        model: str = "auto",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.provider = provider
        self.model = model

        if provider == "minimax":
            # OpenClaw 平台 LLM 网关（agent 内部直接用）
            # 不需要 base_url / api_key，平台已配置
            self.api_key = api_key or "platform-managed"
            self.base_url = base_url or "platform-managed"
            self.model = "minimax/auto"
            self._mode = "platform"
        elif provider == "deepseek":
            self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or self._load_from_config()
            self.base_url = base_url or "https://api.deepseek.com/v1"
            self.model = model or "deepseek-chat"
            self._mode = "http"
        else:
            # 自定义 OpenAI 兼容
            self.api_key = api_key or ""
            self.base_url = base_url or "https://api.openai.com/v1"
            self.model = model or "gpt-4"
            self._mode = "http"

    def _load_from_config(self) -> str:
        try:
            import json
            from pathlib import Path
            config_path = Path("/workspace/config.json")
            if config_path.exists():
                cfg = json.loads(config_path.read_text())
                return cfg.get("deepseek", {}).get("api_key", "")
        except Exception:
            pass
        return ""

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        response_format: Optional[str] = "json",
    ) -> Dict[str, Any]:
        """
        调用 LLM
        返回: {"content": str, "usage": {...}, "model": str}
        """
        if self._mode == "platform":
            return self._chat_platform(system_prompt, user_prompt, temperature, max_tokens)
        else:
            return self._chat_http(system_prompt, user_prompt, temperature, max_tokens, response_format)

    # ---------- 平台 LLM（minimax/auto）----------
    def _chat_platform(self, system_prompt: str, user_prompt: str,
                       temperature: float, max_tokens: int) -> Dict[str, Any]:
        """
        通过 OpenClaw 平台 LLM 网关调用。
        平台已自动注入 base_url/api_key/model 配置，直接调 llm-task 即可。

        若平台 llm_task 不可用，自动 fallback 到 DeepSeek。
        """
        try:
            from llm_task import llm_task  # 平台内置
        except ImportError:
            # Fallback 到 DeepSeek
            return self._fallback_deepseek(system_prompt, user_prompt, temperature, max_tokens)

        full_prompt = f"""{system_prompt}

---

{user_prompt}

---
（请按 system prompt 定义的 JSON Schema 输出，temperature={temperature}, max_tokens={max_tokens}）"""

        try:
            result = llm_task(
                prompt=full_prompt,
                temperature=temperature,
                maxTokens=max_tokens,
            )
        except Exception as e:
            return self._fallback_deepseek(system_prompt, user_prompt, temperature, max_tokens, reason=str(e))

        # llm_task 返回 JSON 字符串
        if isinstance(result, str):
            try:
                content = json.loads(result)
                return {"content": content, "model": self.model, "mode": "platform"}
            except Exception:
                return {"content": result, "model": self.model, "mode": "platform"}
        return {"content": result, "model": self.model, "mode": "platform"}

    def _fallback_deepseek(self, system_prompt: str, user_prompt: str,
                           temperature: float, max_tokens: int,
                           reason: str = "") -> Dict[str, Any]:
        """Fallback：调 DeepSeek API"""
        import urllib.request
        key = self._load_from_config()
        if not key:
            return {
                "content": json.dumps({"error": "no_deepseek_key", "fallback_reason": reason}),
                "model": "deepseek-chat",
                "mode": "fallback-error",
            }
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            return {"content": content, "model": "deepseek-chat", "mode": "fallback-deepseek", "fallback_reason": reason}
        except Exception as e:
            return {
                "content": json.dumps({"error": str(e), "fallback_reason": reason}),
                "model": "deepseek-chat",
                "mode": "fallback-failed",
            }

    # ---------- HTTP LLM（DeepSeek / OpenAI 兼容）----------
    def _chat_http(self, system_prompt: str, user_prompt: str,
                   temperature: float, max_tokens: int,
                   response_format: Optional[str]) -> Dict[str, Any]:
        import urllib.request
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            **({"response_format": {"type": "json_object"}} if response_format == "json" else {}),
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {"content": content, "usage": usage, "model": self.model, "mode": "http"}


# ---------- 全局单例 ----------
_client: Optional[LLMClient] = None


def get_client(provider: str = "minimax") -> LLMClient:
    """获取全局 LLM 客户端（懒加载）"""
    global _client
    if _client is None or _client.provider != provider:
        _client = LLMClient(provider=provider)
    return _client


# ---------- 便捷调用 ----------
def chat(system_prompt: str, user_prompt: str, **kwargs) -> Dict[str, Any]:
    """便捷调用，默认 minimax/auto"""
    return get_client().chat(system_prompt, user_prompt, **kwargs)


if __name__ == "__main__":
    # 快速测试
    import sys
    sys.path.insert(0, "/workspace/src/decision_engine")
    r = chat("你是一个助手", "你好，请用 JSON 返回 {\"reply\": \"...\"}")
    print(json.dumps(r, ensure_ascii=False, indent=2))