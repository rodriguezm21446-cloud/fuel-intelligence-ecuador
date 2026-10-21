from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass

from .text import collapse_spaces, tidy_lines, trim_x


EDITORIAL_GUARDRAILS = (
    "No presentes correlación como causalidad. Una variación de WTI o Brent no demuestra por sí sola "
    "que cambiará el precio al consumidor en Ecuador. Solo afirma una subida, bajada o causa nacional "
    "cuando la evidencia incluya el documento oficial aplicable. No atribuyas un movimiento a un ataque, "
    "oleoducto, sanción, inventario o decisión de la OPEP si esa causa no aparece explícitamente en la evidencia. "
    "Separa hechos, cálculos propios y contexto."
)

# A calm, neutral analyst contract: the model reasons over verified data and states
# its own uncertainty instead of narrating the news or guessing the future.
ANALYST_CONTRACT = (
    "Actúa como analista de riesgo de un fondo: frío, neutral, cuantitativo y sin exagerar. Escribe con "
    "tranquilidad, sin lenguaje de alarma. Recuerda que Ecuador no fija el precio mundial del petróleo: es "
    "tomador de precio, y su precio interno depende de un decreto del Gobierno. Estructura el 'analysis' en "
    "cuatro bloques con estos encabezados exactos: 'Hecho:', 'Drivers medidos:', 'Canal hacia Ecuador:' e "
    "'Incertidumbre:'. En 'Drivers medidos' separa lo medido de lo inferido y escribe 'no verificado' cuando "
            "el vínculo no esté en la evidencia. En 'Incertidumbre' declara de forma explícita lo que no puede saberse "
            "con la evidencia, en particular cuándo y cuánto cambiará el precio oficial en Ecuador."
    )

# The summary is the message the operator reads first: a short Spanish flash
# (≤400 characters, up to three lines) with the necessary data, never a
# paraphrase without figures. Line breaks stay so the flash is readable.
SUMMARY_CONTRACT = (
    "Además, en 'summary' escribe un flash en español de máximo 400 caracteres con hasta tres líneas, "
    "al estilo de un tiquete de mercado: línea 1 con EL HECHO en mayúsculas exprimido del titular de la "
    "fuente (misma sustancia, no una interpretación), línea 2 con las cifras exactas (unidad y vigencia) "
    "copiadas de la evidencia, línea 3 con el canal hacia Ecuador solo si la evidencia lo respalda. "
    "Prohibido: opinión, contexto narrativo sin cifras, mencionar una cifra que no esté en la evidencia. "
    "Si la conexión con Ecuador es débil, omite la línea 3 en lugar de inventarla."
)


def _numbers(value: str) -> Counter[str]:
    """Extract comparable numeric tokens so a rewrite cannot silently drop a figure."""
    return Counter(token.replace(",", ".") for token in re.findall(r"\d+(?:[.,]\d+)?", value))


def _entities(text: str) -> list[str]:
    """Extrae entidades (palabras capitalizadas) y números del texto fuente."""
    return re.findall(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b|\d+(?:[.,]\d+)?", text or "")


def fact_check_summary(summary_text: str, source_text: str) -> tuple[bool, str, list[str]]:
    """Verifica que todo número y entidad del resumen aparezca en el texto fuente.

    Devuelve (ok, motivo, faltantes). Si falta algo, el resumen no es fiable.
    """
    if not summary_text or not source_text:
        return True, "", []
    summary_tokens = set(_entities(summary_text))
    source_tokens = set(_entities(source_text))
    missing = sorted(t for t in summary_tokens if t not in source_tokens)
    if missing:
        return False, f"datos no presentes en la fuente: {', '.join(missing[:5])}", missing
    return True, "", []


def extractive_summary(source_text: str, limit: int = 400) -> str:
    """Resumen extractivo: toma la oración con más datos numéricos y entidades clave.

    Nunca inventa cifras: solo reordena/corta lo que ya está en la fuente.
    """
    sentences = re.split(r"(?<=[.!?])\s+", collapse_spaces(source_text or ""))
    if not sentences:
        return ""
    scored: list[tuple[int, str]] = []
    for s in sentences:
        score = len(_numbers(s)) * 2 + len(_entities(s))
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    return trim_x(best, limit)


@dataclass(frozen=True, slots=True)
class Editorial:
    analysis: str
    x_text: str
    doubts: str = ""
    summary: str = ""


class GeminiEditor:
    """Optional Gemini reviewer; discovery and verification stay deterministic."""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 60):
        self.api_key = api_key.strip()
        # The editorial run needs writing quality: default to the full flash
        # tier rather than the smallest -lite model. FUELINTEL_GEMINI_MODEL /
        # GEMINI_MODEL overrides remain fully honored.
        fallback = os.getenv("FUELINTEL_GEMINI_MODEL", "").strip()
        selected = model.strip() or fallback or "gemini-3.5-flash"
        self.model = selected if selected.replace("-", "").replace(".", "").isalnum() else "gemini-3.5-flash"
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
            "documentos oficiales pendientes. Escribe sin emojis. Si se entregan DATOS INTERNOS VERIFICADOS, "
            "intégralos de forma fría y ordenada para explicar el contexto ecuatoriano. No escribas ninguna "
            "cifra (años, horas, porcentajes, conteos ni numeración de listas) que no aparezca literalmente en la "
            "evidencia. El análisis tendrá máximo 1100 caracteres y el texto para X máximo 280. "
            f"{ANALYST_CONTRACT} {SUMMARY_CONTRACT} {EDITORIAL_GUARDRAILS}\n\n"
            f"EVIDENCIA VERIFICADA:\n{evidence[:12000]}\n\n"
            f"BORRADOR SEGURO PARA X:\n{fallback_x}\n\n"
            "Conserva exactamente las cifras comprobadas."
        )
        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "analysis": {"type": "string"},
                "x_text": {"type": "string"},
                "doubts": {"type": "string"},
            },
            "required": ["summary", "analysis", "x_text", "doubts"],
        }
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                # Four editorial fields exceed 2000 characters in Spanish; the
                # old budget of 1000 tokens truncated the JSON mid-field and
                # silently discarded every editorial.
                "maxOutputTokens": 3000,
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
            analysis = tidy_lines(str(parsed.get("analysis", "")))[:1100].strip()
            x_text = trim_x(collapse_spaces(str(parsed.get("x_text", ""))), 280)
            doubts = collapse_spaces(str(parsed.get("doubts", "")))[:300].strip()
            # Keep the flash's line structure for readability; tighten runs of
            # spaces inside each line only. Hard cap stays at 400 characters.
            summary = tidy_lines(str(parsed.get("summary", "")))[:400].strip() or x_text
            if not analysis or not x_text:
                self.last_error = "Gemini devolvió campos editoriales vacíos"
                return None
            allowed_numbers = _numbers(evidence + "\n" + fallback_x)
            generated_numbers = _numbers(analysis + "\n" + x_text + "\n" + doubts + "\n" + summary)
            # The same verified figure may legitimately appear in both the
            # analysis and the X text; only brand-new figures are rejected.
            invented_numbers = [token for token in generated_numbers if token not in allowed_numbers]
            if invented_numbers:
                self.last_error = "respuesta descartada: Gemini añadió cifras no presentes en la evidencia"
                return None
            required_x_numbers = _numbers(fallback_x)
            generated_x_numbers = _numbers(x_text)
            if any(generated_x_numbers[token] < count for token, count in required_x_numbers.items()):
                self.last_error = "respuesta descartada: Gemini omitió cifras del borrador verificado"
                return None
            return Editorial(analysis=analysis, x_text=x_text, doubts=doubts, summary=summary)
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

    def translate(self, text: str, target_language: str = "es") -> str | None:
        """Translate a foreign headline/summary to Spanish, preserving every figure.

        The engine is the preferred one; when it is unavailable or drops a figure
        the caller falls back to a local engine so no raw English reaches the user.
        """
        self.last_error = ""
        if not self.enabled:
            self.last_error = "GEMINI_API_KEY no está configurada"
            return None
        clean = collapse_spaces(text or "")
        if not clean:
            return None
        prompt = (
            f"Traduce al {target_language} el siguiente texto de una noticia de combustibles. "
            "Conserva intactos TODOS los números, fechas, porcentajes, unidades, nombres propios, "
            "tickers y siglas: no los redondees, omitas ni traduzcas. No resumas, no añadas, no expliques. "
            "Devuelve únicamente la traducción, sin comillas ni comentarios.\n\n"
            f"TEXTO:\n{clean[:4000]}"
        )
        schema = {
            "type": "object",
            "properties": {"translation": {"type": "string"}},
            "required": ["translation"],
        }
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1000,
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
            translation = collapse_spaces(str(parsed.get("translation", ""))).strip()
            if not translation:
                self.last_error = "Gemini devolvió una traducción vacía"
                return None
            source_numbers = _numbers(clean)
            translated_numbers = _numbers(translation)
            if any(translated_numbers[token] < count for token, count in source_numbers.items()):
                self.last_error = "traducción descartada: se perdieron cifras del original"
                return None
            return translation
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
