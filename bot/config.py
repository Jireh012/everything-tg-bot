from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    everything_base_url: str
    local_bot_api_url: str
    download_dir: str
    use_file_uri: bool
    page_size: int
    max_results: int
    session_ttl_seconds: int
    search_rate_per_minute: int
    inline_search_rate_per_minute: int
    download_rate_per_minute: int
    max_concurrent_downloads: int
    max_file_size_bytes: int

    @property
    def bot_api_base_url(self) -> str:
        return f"{self.local_bot_api_url.rstrip('/')}/bot"

    @property
    def bot_api_base_file_url(self) -> str:
        return f"{self.local_bot_api_url.rstrip('/')}/file/bot"


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 BOT_TOKEN，请复制 .env.example 为 .env 并填写")

    return Config(
        bot_token=token,
        everything_base_url=os.getenv(
            "EVERYTHING_BASE_URL", "http://www.https.ng:1234"
        ).rstrip("/"),
        local_bot_api_url=os.getenv(
            "LOCAL_BOT_API_URL", "http://127.0.0.1:8081"
        ).rstrip("/"),
        download_dir=os.getenv("DOWNLOAD_DIR", "/tmp/bot-downloads"),
        use_file_uri=_bool("USE_FILE_URI", True),
        page_size=_int("PAGE_SIZE", 10),
        max_results=_int("MAX_RESULTS", 50),
        session_ttl_seconds=_int("SESSION_TTL_SECONDS", 1800),
        search_rate_per_minute=_int("SEARCH_RATE_PER_MINUTE", 10),
        inline_search_rate_per_minute=_int("INLINE_SEARCH_RATE_PER_MINUTE", 30),
        download_rate_per_minute=_int("DOWNLOAD_RATE_PER_MINUTE", 3),
        max_concurrent_downloads=_int("MAX_CONCURRENT_DOWNLOADS", 5),
        max_file_size_bytes=_int("MAX_FILE_SIZE_BYTES", 2 * 1024 * 1024 * 1024),
    )
