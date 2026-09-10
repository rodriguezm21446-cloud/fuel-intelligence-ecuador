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


class GeminiEditor:
    """Optional Gemini reviewer; discovery and verification stay deterministic."""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 30):
        self.api_key = api_key.strip()
        selected = model.strip() or "gemini-3.5-flash-lite"
        self.model = selected if selected.replace("-", "").replace(".", "").isalnum() else "gemini-3.5-flash-lite"
        self.timeout_seconds = timeout_seconds
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def review(self, evidence: str, fallback_x: str) -> Editorial | None:
        self.last_error = ""
        if not self.enabled:
            self.last_error = "GEMINI_API_KEY no está configurada"
            return None
        prompt = (
            "Eres editor de inteligencia de combustibles de Ecuador. Usa únicamente la evidencia. "
            "No inventes cifras, fechas, causas, predicciones ni fuentes. Declara contradicciones o "
            "documentos oficiales pendientes. Escribe sin emojis. El análisis tendrá máximo 700 "
            "caracteres y el texto para X máximo 280.\n\n"
            f"EVIDENCIA VERIFICADA:\n{evidence[:12000]}\n\n"
            f"BORRADOR SEGURO PARA X:\n{fallback_x}\n\n"
            "Conserva exactamente las cifras comprobadas."
        )
        schema = {
            "type": "object",
            "properties": {
                "analysis": {"type": "string"},
                "x_text": {"type": "string"},
                "doubts": {"type": "string"},
            },
            "required": ["analysis", "x_text", "doubts"],
        }
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 700,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "FuelIntelligenceEcuador/0.3",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
            content = raw["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(content)
            analysis = collapse_spaces(str(parsed.get("analysis", "")))[:700].strip()
            x_text = trim_x(collapse_spaces(str(parsed.get("x_text", ""))), 280)
            doubts = collapse_spaces(str(parsed.get("doubts", "")))[:300].strip()
            if not analysis or not x_text:
                return None
            return Editorial(analysis, x_text, doubts)
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8", errors="replace"))
                message = detail.get("error", {}).get("message", "respuesta rechazada")
            except (TypeError, ValueError):
                message = "respuesta rechazada"
            self.last_error = f"HTTP {error.code}: {message}"[:500]
            return None
        except (KeyError, IndexError, TypeError, ValueError, TimeoutError, urllib.error.URLError) as error:
            self.last_error = f"{type(error).__name__}: {error}"[:500]
            return None

