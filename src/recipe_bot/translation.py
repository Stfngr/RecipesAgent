import re

import httpx

from .api import APIError, request_json


_FAHRENHEIT = re.compile(r"\b(\d{3})\s*(?:°\s*F\b|degrees?\s+(?:F\b|Fahrenheit\b))", re.IGNORECASE)


def metric_temperatures(text: str) -> str:
    return _FAHRENHEIT.sub(
        lambda match: f"{round((int(match[1]) - 32) / 9) * 5} °C", text
    )


class LibreTranslateTranslator:
    def __init__(self, client: httpx.AsyncClient, url: str, target: str, timeout: int):
        self.client = client
        self.url = url.rstrip("/") + "/translate"
        self.target = target
        self.timeout = timeout

    async def translate(self, texts: list[str]) -> list[str]:
        data = await request_json(
            self.client, "POST", self.url, "LibreTranslate",
            json={"q": texts, "source": "en", "target": self.target, "format": "text"},
            timeout=httpx.Timeout(self.timeout, connect=10),
        )
        translations = data.get("translatedText")
        if not isinstance(translations, list) or len(translations) != len(texts):
            raise APIError("LibreTranslate validation")
        for source, target in zip(texts, translations):
            if (not isinstance(target, str) or not target.strip() or len(target) > 4 * len(source) + 200
                    or re.findall(r"\d+", source) != re.findall(r"\d+", target)):
                raise APIError("LibreTranslate validation")
        return translations
