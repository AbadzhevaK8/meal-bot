import json
import logging
import re
from typing import Any

from openai import OpenAI

from config import load_config

logger = logging.getLogger(__name__)
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _extract_json_from_text(text: str) -> str | None:
    """Извлекает JSON-объект из текста, убирая thinking-блоки, markdown и пояснения."""
    # Удаляем thinking-блоки (<thinking>...</thinking>)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    text = text.strip()

    # Убираем markdown-блоки (```json ... ```)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Ищем сбалансированный JSON-объект в тексте
    brace_depth = 0
    json_start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if json_start == -1:
                json_start = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and json_start != -1:
                return text[json_start:i + 1]

    return text if text.startswith('{') else None


def _parse_response(text: str) -> dict[str, Any] | None:
    json_str = _extract_json_from_text(text)
    if json_str is None:
        logger.error("DeepSeek вернул текст без JSON: %s", text[:200])
        return None

    try:
        payload = json.loads(json_str)
    except json.JSONDecodeError:
        logger.error("DeepSeek вернул невалидный JSON: %s", json_str[:200])
        return None

    if not isinstance(payload, dict):
        logger.error("DeepSeek вернул не словарь: %s", type(payload))
        return None

    return _normalize_meal_result(payload)


def _normalize_meal_result(payload: dict[str, Any]) -> dict[str, Any]:
    mapped = {
        "name": payload.get("name") or payload.get("dish") or payload.get("product") or "Еда",
        "weight_g": _to_int(payload.get("weight_g") or payload.get("weight") or payload.get("grams") or 0),
        "kcal": _to_float(payload.get("kcal") or payload.get("calories") or payload.get("energy") or 0),
        "protein_g": _to_float(payload.get("protein_g") or payload.get("proteins") or payload.get("protein") or 0),
        "fat_g": _to_float(payload.get("fat_g") or payload.get("fat") or payload.get("fats") or 0),
        "carbs_g": _to_float(payload.get("carbs_g") or payload.get("carbohydrates") or payload.get("carbs") or 0),
        "confidence": payload.get("confidence") or payload.get("confidence_level") or "low",
        "note": payload.get("note") or payload.get("comment") or payload.get("description") or "",
    }
    return mapped


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def analyze_text_food(text: str) -> dict[str, Any] | None:
    config = load_config()
    if not config.DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API key is not configured")
        return None

    base_url = config.DEEPSEEK_API_URL or DEFAULT_DEEPSEEK_BASE_URL
    logger.info("DeepSeek text analysis for user text: %s", text[:120])
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=base_url)

    prompt = (
        "Ты нутрициолог-аналитик. Тебе присылают текстовое описание еды. "
        "Проанализируй блюдо и верни ТОЛЬКО валидный JSON без markdown и пояснений:\n"
        "{\n  \"name\": \"название блюда/продукта\",\n  \"weight_g\": 250,\n  \"kcal\": 380,\n  \"protein_g\": 35,\n  \"fat_g\": 8,\n  \"carbs_g\": 42,\n  \"confidence\": \"high|medium|low\",\n  \"note\": \"опциональный комментарий если что-то неочевидно\"\n}"
    )

    try:
        response = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            temperature=0,
        )

        content = response.choices[0].message.content
        return _parse_response(content)
    except Exception as e:
        logger.exception("Ошибка при обращении к DeepSeek: %s", e)
        return None
