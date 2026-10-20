from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from fuelintel.ai import ANALYST_CONTRACT, EDITORIAL_GUARDRAILS, Editorial, GeminiEditor, extractive_summary, fact_check_summary


class GeminiTests(unittest.TestCase):
    def test_guardrail_rejects_automatic_brent_causality(self):
        self.assertIn("no demuestra por sí sola", EDITORIAL_GUARDRAILS)
        self.assertIn("documento oficial", EDITORIAL_GUARDRAILS)
        self.assertIn("oleoducto", EDITORIAL_GUARDRAILS)

    def test_analyst_contract_requires_structure_and_declared_uncertainty(self):
        self.assertIn("Incertidumbre:", ANALYST_CONTRACT)
        self.assertIn("tomador de precio", ANALYST_CONTRACT)

    @patch("fuelintel.ai.urllib.request.urlopen")
    def test_structured_analysis_keeps_its_sections(self, mocked):
        content = json.dumps({
            "analysis": (
                "Hecho: el precio se mantiene.\n"
                "Drivers medidos: no verificado.\n"
                "Incertidumbre: no puede saberse cuándo cambiará."
            ),
            "x_text": "Precio sin cambio por ahora.",
            "doubts": "",
        })
        mocked.return_value = io.BytesIO(json.dumps({
            "candidates": [{"content": {"parts": [{"text": content}]}}]
        }).encode("utf-8"))
        editor = GeminiEditor("secret", "gemini-3.5-flash-lite")
        editorial = editor.review("El precio se mantiene sin cambio.", "Precio sin cambio por ahora.")
        self.assertIsNotNone(editorial)
        self.assertEqual(editorial.analysis.split("\n")[0], "Hecho: el precio se mantiene.")
        self.assertIn("Incertidumbre:", editorial.analysis)

    def test_is_disabled_without_secret(self):
        editor = GeminiEditor("", "gemini-3.5-flash-lite")
        self.assertFalse(editor.enabled)
        self.assertIsNone(editor.review("evidence", "safe draft"))

    @patch("fuelintel.ai.urllib.request.urlopen")
    def test_timeout_falls_back_without_blocking(self, mocked):
        mocked.side_effect = TimeoutError()
        editor = GeminiEditor("secret", "gemini-3.5-flash-lite")
        self.assertIsNone(editor.review("evidence", "safe draft"))

    @patch("fuelintel.ai.urllib.request.urlopen")
    def test_discards_numbers_invented_by_the_model(self, mocked):
        content = json.dumps({"analysis": "El precio subirá 99%.", "x_text": "Subirá 99%.", "doubts": ""})
        mocked.return_value = io.BytesIO(json.dumps({
            "candidates": [{"content": {"parts": [{"text": content}]}}]
        }).encode("utf-8"))
        editor = GeminiEditor("secret", "gemini-3.5-flash-lite")
        self.assertIsNone(editor.review("Extra cuesta $3.21.", "Extra: $3.21."))
        self.assertIn("añadió cifras", editor.last_error)

    @patch("fuelintel.ai.urllib.request.urlopen")
    def test_discards_model_text_that_omits_a_verified_figure(self, mocked):
        content = json.dumps({"analysis": "Precios comprobados.", "x_text": "Extra: $3.21.", "doubts": ""})
        mocked.return_value = io.BytesIO(json.dumps({
            "candidates": [{"content": {"parts": [{"text": content}]}}]
        }).encode("utf-8"))
        editor = GeminiEditor("secret", "gemini-3.5-flash-lite")
        self.assertIsNone(editor.review("Extra $3.21 y diésel $4.50.", "Extra $3.21 | Diésel $4.50."))
        self.assertIn("omitió cifras", editor.last_error)


class FactCheckTests(unittest.TestCase):
    def test_summary_with_all_numbers_passes(self):
        summary = "El precio subió a $3.21 el 12 de octubre."
        source = "El precio del combustible es de $3.21 desde el 12 de octubre."
        ok, reason, missing = fact_check_summary(summary, source)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(missing, [])

    def test_summary_with_invented_number_fails(self):
        summary = "El precio subió a $9.99 el 12 de octubre."
        source = "El precio del combustible es de $3.21 desde el 12 de octubre."
        ok, reason, missing = fact_check_summary(summary, source)
        self.assertFalse(ok)
        self.assertIn("9.99", missing)

    def test_extractive_summary_picks_sentence_with_numbers(self):
        text = "La gasolina sube. El diésel está a $3.21 desde octubre. No hay novedades."
        result = extractive_summary(text)
        self.assertIn("$3.21", result)
        self.assertLessEqual(len(result), 400)

    def test_extractive_summary_empty_input(self):
        self.assertEqual(extractive_summary(""), "")

    def test_extractive_summary_no_numbers_picks_first(self):
        text = "Solo hay texto aquí. Nada de números."
        result = extractive_summary(text)
        self.assertIn("Solo hay texto aquí.", result)


if __name__ == "__main__":
    unittest.main()
