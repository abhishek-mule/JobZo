import json
import logging
from typing import Any
from pathlib import Path
import hashlib

from services.config import Config

logger = logging.getLogger("jobzo.ai")

CACHE_DIR = Path(__file__).parent.parent / "cache" / "llm"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class AIClient:
    def __init__(self):
        cfg = Config.llm_config()
        self.provider_name = cfg.get("provider", "ollama")
        self.ollama_cfg = cfg.get("ollama", {})
        self.openai_cfg = cfg.get("openai", {})
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if self.provider_name == "openai":
                import httpx
                import openai
                self._client = openai.OpenAI(
                    api_key=self.openai_cfg.get("api_key", ""),
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    max_retries=0,
                )
            else:
                import httpx
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self.ollama_cfg.get("base_url", "http://localhost:11434/v1"),
                    api_key="ollama",
                    timeout=httpx.Timeout(300.0, connect=10.0),
                    max_retries=0,
                )
        return self._client

    def _model(self) -> str:
        if self.provider_name == "openai":
            return self.openai_cfg.get("model", "gpt-4o-mini")
        return self.ollama_cfg.get("model", "qwen3:4b")

    def _cache_key(self, prompt: str, model: str) -> str:
        raw = f"{model}:{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str):
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None

    def _cache_set(self, key: str, data: Any):
        path = CACHE_DIR / f"{key}.json"
        with open(path, "w") as f:
            json.dump(data, f)

    def ask(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type | None = None,
        use_cache: bool = True,
    ) -> Any:
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        model = self._model()

        if use_cache:
            key = self._cache_key(full_prompt, model)
            cached = self._cache_get(key)
            if cached:
                logger.debug("AI cache hit")
                if response_model:
                    return response_model.model_validate(cached)
                return cached

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.info("Calling AI model: %s", model)
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
        }
        if response_model and self.provider_name == "openai":
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content.strip()
        content = content.removeprefix("```json").removesuffix("```").strip()

        try:
            parsed = json.loads(content) if content else {}
        except json.JSONDecodeError:
            logger.error("AI returned invalid JSON: %s", content[:200])
            raise

        if use_cache:
            self._cache_set(key, parsed)

        if response_model:
            return response_model.model_validate(parsed)
        return parsed
