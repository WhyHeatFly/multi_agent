import json
from typing import Any

import httpx

from app.core.config import Settings


class LLMAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure_configured(self) -> None:
        if not self.settings.llm_configured:
            raise RuntimeError("LLM_API_KEY 未配置，无法真实生成文化 IP 任务。请在 .env 中配置 LLM_API_KEY 后重启服务。")

    def chat_json_sync(self, messages: list[dict[str, str]], temperature: float = 0.7) -> dict[str, Any]:
        self.ensure_configured()
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        }
        with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            return content
        return json.loads(content)

    async def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        self.ensure_configured()
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            return content
        return json.loads(content)
