import logging
from pathlib import Path

from ai.client import AIClient
from ai.validator import VALIDATORS

logger = logging.getLogger("jobzo.llm")
PROMPT_DIR = Path(__file__).parent.parent / "prompts"
PROMPT_DIR.mkdir(exist_ok=True)


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if path.exists():
        return path.read_text().strip()
    logger.warning("Prompt not found: %s", name)
    return ""


def ask(
    prompt_name: str,
    user_message: str,
    use_cache: bool = True,
) -> str | dict:
    system_prompt = _load_prompt(f"{prompt_name}.txt")
    if not system_prompt:
        system_prompt = "You are a helpful assistant."

    client = AIClient()
    response_model = VALIDATORS.get(prompt_name)

    result = client.ask(
        system_prompt=system_prompt,
        user_prompt=user_message,
        response_model=response_model,
        use_cache=use_cache,
    )

    return result.model_dump() if response_model else result
