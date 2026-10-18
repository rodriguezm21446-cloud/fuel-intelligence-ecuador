from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import SourceSpec


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE settings without replacing values already in the environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value.strip())


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sí", "si"}


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    sources_file: Path
    timezone_name: str
    dashboard_hours: int
    intraday_enabled: bool
    intraday_interval_seconds: int
    intraday_alert_cooldown_minutes: int
    news_max_age_hours: int
    ecuador_only: bool
    request_timeout_seconds: int
    supabase_sync_minutes: int
    dry_run: bool
    telegram_enabled: bool
    telegram_token: str
    telegram_chat_id: str
    supabase_url: str
    supabase_service_key: str
    eia_api_key: str
    gemini_api_key: str
    gemini_model: str
    chart_revision: str

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls) -> "Settings":
        env_path = Path(os.getenv("FUELINTEL_ENV_FILE", "data/secrets/telegram.env"))
        load_env_file(env_path)
        return cls(
            data_dir=Path(os.getenv("FUELINTEL_DATA_DIR", "./data")),
            sources_file=Path(os.getenv("FUELINTEL_SOURCES_FILE", "./config/sources.json")),
            timezone_name=os.getenv("FUELINTEL_TIMEZONE", "America/Guayaquil"),
            dashboard_hours=int(os.getenv("FUELINTEL_DASHBOARD_HOURS", "5")),
            intraday_enabled=_bool("FUELINTEL_INTRADAY_ENABLED", True),
            intraday_interval_seconds=max(60, int(os.getenv("FUELINTEL_INTRADAY_INTERVAL_SECONDS", "60"))),
            intraday_alert_cooldown_minutes=max(
                5, int(os.getenv("FUELINTEL_INTRADAY_ALERT_COOLDOWN_MINUTES", "30"))
            ),
            news_max_age_hours=int(os.getenv("FUELINTEL_NEWS_MAX_AGE_HOURS", "36")),
            ecuador_only=_bool("FUELINTEL_ECUADOR_ONLY", True),
            request_timeout_seconds=int(os.getenv("FUELINTEL_REQUEST_TIMEOUT_SECONDS", "20")),
            supabase_sync_minutes=int(os.getenv("FUELINTEL_SUPABASE_SYNC_MINUTES", "10")),
            dry_run=_bool("FUELINTEL_DRY_RUN", True),
            telegram_enabled=_bool("FUELINTEL_TELEGRAM_ENABLED", False),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
            supabase_service_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            eia_api_key=os.getenv("EIA_API_KEY", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            chart_revision=os.getenv("FUELINTEL_CHART_REVISION", "1").strip() or "1",
        )

    def validate_activation(self) -> None:
        if self.telegram_enabled and not self.dry_run:
            if not self.telegram_token or not self.telegram_chat_id:
                raise ValueError("Telegram está activo pero faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")


def load_sources(path: Path) -> list[SourceSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sources = [SourceSpec.from_dict(item) for item in raw]
    ids = [source.id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("Hay identificadores de fuentes duplicados")
    return [source for source in sources if source.enabled]


def load_x_sources(path: Path) -> dict[str, SourceSpec]:
    """Sources dispatched by the optional X adapter, never polled as HTML."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {item["source_id"]: SourceSpec(
        id=item["source_id"], name=f"X · @{item['username']}",
        url=f"https://x.com/{item['username']}", kind="x", trust=item["trust"],
        country=item["country"], max_age_hours=24
    ) for item in raw["accounts"]}
