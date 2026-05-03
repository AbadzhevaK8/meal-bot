import base64
import io
import json
import logging
from typing import Any

from groq import AsyncGroq
from PIL import Image

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты нутрициолог-аналитик. Тебе присылают фото еды.
Возможные типы фото:
- упаковка с этикеткой → используй данные с этикетки точно
- блюдо на тарелке → оцени состав и типичный вес порции
- продукт на весах → вес может быть указан в подписи

Если в подписи есть вес или уточнение — используй его.

ВАЖНО: Все значения КБЖУ (kcal, protein_g, fat_g, carbs_g) должны быть
рассчитаны НА УКАЗАННЫЙ ВЕС ПОРЦИИ (weight_g), а не на 100г.
Например, если вес порции 35г, а на 100г продукта 351 ккал,
то kcal должно быть 123 (351 * 35 / 100).

Верни ТОЛЬКО валидный JSON без markdown:
{
  "name": "название блюда/продукта",
  "weight_g": 250,
  "kcal": 380,
  "protein_g": 35,
  "fat_g": 8,
  "carbs_g": 42,
  "confidence": "high|medium|low",
  "note": "опциональный комментарий если что-то неочевидно"
}"""


def _parse_response(text: str) -> dict[str, Any] | None:
    """Пытается распарсить JSON из ответа Gemini."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


async def analyze_food(image_bytes: bytes, caption: str | None) -> dict[str, Any] | None:
    """
    Анализирует фото еды через Groq Vision (llama-4-scout).

    Args:
        image_bytes: Байты изображения.
        caption: Подпись к фото (может содержать вес или уточнение).

    Returns:
        Словарь с данными о еде или None при ошибке.
    """
    from config import load_config

    config = load_config()
    client = AsyncGroq(api_key=config.GROQ_API_KEY)

    # Конвертируем в JPEG и кодируем в base64
    image = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG")
    image_b64 = base64.b64encode(buf.getvalue()).decode()

    user_text = "Проанализируй еду на фото."
    if caption:
        user_text += f" Подпись: {caption}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
                {"type": "text", "text": user_text},
            ],
        },
    ]

    for attempt in range(2):
        try:
            response = await client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.1,
                max_tokens=512,
            )
            text = response.choices[0].message.content

            result = _parse_response(text)
            if result is not None:
                return result

            if attempt == 0:
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "Верни только JSON"})
                logger.warning("Groq вернул не JSON, повторяю запрос")
            else:
                logger.error("Groq повторно вернул не JSON: %s", text[:200])
                return None

        except Exception as e:
            logger.exception("Ошибка при запросе к Groq (попытка %d): %s", attempt + 1, e)
            if attempt == 1:
                return None
