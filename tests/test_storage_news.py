from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fuelintel.storage import Store


class RecentMarketNewsTests(unittest.TestCase):
    def test_only_returns_recent_relevant_notified_items(self):
        now = datetime(2026, 9, 13, 15, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "db.sqlite3")
            with store.connect() as db:
                for index, (hours, score, notified) in enumerate(((2, 80, True), (2, 20, True), (60, 90, True), (1, 90, False))):
                    detected = (now - timedelta(hours=hours)).isoformat()
                    db.execute(
                        """INSERT INTO items(content_hash, canonical_url, source_id, source_name, source_trust,
                        title, normalized_title, summary, detected_at, status, confidence, reasons_json,
                        relevance_score, notified_at) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 'VERIFIED', 90, '[]', ?, ?)""",
                        (f"hash-{index}", f"https://example.com/{index}", f"s{index}", "EIA", 90,
                         f"Story {index}", f"story {index}", detected, score, detected if notified else None),
                    )
            rows = store.recent_notified_market_news(now=now)
            self.assertEqual([row["title"] for row in rows], ["Story 0"])


if __name__ == "__main__":
    unittest.main()
