from pathlib import Path
import yaml
from typing import Any

CONFIG_DIR = Path(__file__).parent.parent / "config"


class Config:
    _data: dict[str, Any] = {}

    @classmethod
    def load(cls):
        for yaml_file in CONFIG_DIR.glob("*.yaml"):
            key = yaml_file.stem
            with open(yaml_file) as f:
                cls._data[key] = yaml.safe_load(f)

    @classmethod
    def get(cls, key: str, default=None):
        return cls._data.get(key, default)

    @classmethod
    def provider_config(cls, name: str) -> dict:
        providers = cls._data.get("providers", {})
        return providers.get(name, {})

    @classmethod
    def llm_config(cls) -> dict:
        return cls._data.get("llm", {})

    @classmethod
    def browser_config(cls) -> dict:
        return cls._data.get("browser", {})

    @classmethod
    def resume_config(cls) -> dict:
        return cls._data.get("resume", {})


Config.load()
