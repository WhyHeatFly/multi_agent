from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


@dataclass
class LLMClientResult:
    status: str
    model: str
    content: dict[str, Any] | None = None
    error: str | None = None
    raw_text: str | None = None


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = 30,
        enabled: bool = False,
        max_retries: int = 1,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.max_retries = max_retries

    @classmethod
    def from_env(cls) -> DeepSeekClient:
        env = load_dotenv()
        enabled = env.get("DEMAND_LLM_ENABLED", "false").lower() == "true"
        timeout_raw = env.get("DEMAND_LLM_TIMEOUT_SECONDS", "30")
        try:
            timeout_seconds = int(timeout_raw)
        except ValueError:
            timeout_seconds = 30
        return cls(
            api_key=env.get("DEEPSEEK_API_KEY"),
            base_url=env.get("DEMAND_LLM_BASE_URL", DEFAULT_BASE_URL),
            model=env.get("DEMAND_LLM_MODEL", DEFAULT_MODEL),
            timeout_seconds=timeout_seconds,
            enabled=enabled,
        )

    def complete_json(self, messages: list[dict[str, str]], max_tokens: int = 3000) -> LLMClientResult:
        if not self.enabled:
            return LLMClientResult(status="disabled", model=self.model, error="LLM is disabled")
        if not self.api_key:
            return LLMClientResult(status="failed", model=self.model, error="DEEPSEEK_API_KEY is not configured")

        payload = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                text = response_payload["choices"][0]["message"]["content"]
                if not text:
                    return LLMClientResult(status="invalid_json", model=self.model, error="empty model response")
                try:
                    return LLMClientResult(
                        status="success",
                        model=self.model,
                        content=json.loads(text),
                        raw_text=text,
                    )
                except json.JSONDecodeError as exc:
                    return LLMClientResult(
                        status="invalid_json",
                        model=self.model,
                        error=f"invalid json: {exc.msg}",
                        raw_text=text[:1000],
                    )
            except HTTPError as exc:
                last_error = f"http {exc.code}: {self._safe_http_message(exc)}"
            except (URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
                last_error = self._safe_error(exc)

            if attempt < self.max_retries:
                time.sleep(0.5 * (attempt + 1))

        return LLMClientResult(status="failed", model=self.model, error=last_error or "unknown llm error")

    def _safe_http_message(self, exc: HTTPError) -> str:
        try:
            return exc.read(300).decode("utf-8", errors="replace")
        except Exception:
            return exc.reason if exc.reason else "http error"

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc) or exc.__class__.__name__
        if self.api_key:
            message = message.replace(self.api_key, "[redacted]")
        return message[:500]


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    values = dict(os.environ)
    env_path = Path(path)
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values
