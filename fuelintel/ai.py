from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .text import collapse_spaces, trim_x


@dataclass(frozen=True, slots=True)
class Editorial:
    analysis: str
    x_text: str
    doubts: str = ""


class DeepSeekEditor:
    """Optional editorial reviewer; discovery and verification stay deterministic."""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 30):
        self.api_key = api_key.strip()
        self.model = model.strip() or "deepseek-v4-flash"
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def review(self, evidence: str, fallback_x: str) -> Editorial | None:
        if not self.enabled:
            return None
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Eres editor de inteligencia de combustibles de Ecuador. Usa únicamente la evidencia. "
                        "No inventes cifras, fechas, causas, predicciones ni fuentes. Declara contradicciones o "
                        "documentos oficiales pendientes. Escribe sin emojis. Devuelve solo un objeto json con "
                        "analysis, x_text y doubts. analysis máximo 700 caracteres; x_text máximo 280."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"EVIDENCIA VERIFICADA:\n{evidence[:12000]}\n\n"
                        f"BORRADOR SEGURO PARA X:\n{fallback_x}\n\n"
                        "Genera el json y conserva exactamente las cifras comprobadas."
                    ),
                },
            ],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 700,
            "temperature": 0.1,
            "stream": False,
        }
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "FuelIntelligenceEcuador/0.3",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
            parsed = json.loads(raw["choices"][0]["message"]["content"])
            analysis = collapse_spaces(str(parsed.get("analysis", "")))[:700].strip()
            x_text = trim_x(collapse_spaces(str(parsed.get("x_text", ""))), 280)
            doubts = collapse_spaces(str(parsed.get("doubts", "")))[:300].strip()
            if not analysis or not x_text:
                return None
            return Editorial(analysis, x_text, doubts)
        except (KeyError, IndexError, TypeError, ValueError, TimeoutError, urllib.error.URLError):
            return None

