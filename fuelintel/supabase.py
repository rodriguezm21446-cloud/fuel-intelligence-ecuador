from __future__ import annotations

import json
import urllib.request

from .storage import Store


TABLES = {
    "items": "fuelintel_items",
    "prices": "fuelintel_prices",
}


class SupabaseMirror:
    """Replica la memoria SQLite a Supabase en lotes pequeños y reintentables."""

    def __init__(self, url: str, service_key: str, timeout: int = 30, enabled: bool = True):
        self.url = url.rstrip("/")
        self.service_key = service_key
        self.timeout = timeout
        self.enabled = enabled and bool(self.url and self.service_key)

    def _post(self, table: str, rows: list[dict]) -> None:
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{table}",
            data=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
                "User-Agent": "FuelIntelligenceEcuador/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status not in {200, 201, 204}:
                raise RuntimeError(f"Supabase respondió HTTP {response.status}")

    def sync(self, store: Store, limit: int = 300) -> int:
        if not self.enabled:
            return 0
        pending = store.pending_sync(limit)
        grouped: dict[str, list] = {"items": [], "prices": []}
        for row in pending:
            if row["entity_type"] in grouped:
                grouped[row["entity_type"]].append(row)

        synced = 0
        for entity_type, rows in grouped.items():
            if not rows:
                continue
            ids = [int(row["id"]) for row in rows]
            payloads = [json.loads(row["payload_json"]) for row in rows]
            try:
                self._post(TABLES[entity_type], payloads)
            except Exception as exc:
                store.mark_sync_error(ids, f"{type(exc).__name__}: {exc}")
                raise
            else:
                store.mark_synced(ids)
                synced += len(ids)
        return synced

