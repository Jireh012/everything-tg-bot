from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from bot.config import Config, load_config
from bot.handlers import BotApp

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def _wait_for_local_api(config: Config, attempts: int = 30) -> None:
    url = config.local_bot_api_url.rstrip("/") + "/"
    last_error: Exception | None = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    logging.getLogger(__name__).info("local bot api ready: %s", url)
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                logging.getLogger(__name__).info("local bot api ready: %s", url)
                return
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(1)
            if i == 0 or (i + 1) % 5 == 0:
                logging.getLogger(__name__).info(
                    "waiting for local bot api %s (%s/%s)", url, i + 1, attempts
                )
    raise RuntimeError(f"Local Bot API 未就绪: {url}") from last_error


def main() -> None:
    config = load_config()
    _wait_for_local_api(config)
    app_logic = BotApp(config)
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=600.0,
        write_timeout=600.0,
        pool_timeout=30.0,
    )
    application = (
        Application.builder()
        .token(config.bot_token)
        .base_url(config.bot_api_base_url)
        .base_file_url(config.bot_api_base_file_url)
        .local_mode(True)
        .request(request)
        .get_updates_request(HTTPXRequest(connect_timeout=30.0, read_timeout=60.0))
        .build()
    )
    application.add_handler(CommandHandler("start", app_logic.start))
    application.add_handler(CommandHandler("help", app_logic.start))
    application.add_handler(CommandHandler("search", app_logic.search_cmd))
    application.add_handler(InlineQueryHandler(app_logic.on_inline_query))
    application.add_handler(CallbackQueryHandler(app_logic.on_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, app_logic.on_text)
    )
    logging.getLogger(__name__).info(
        "bot started, everything=%s local_api=%s",
        config.everything_base_url,
        config.local_bot_api_url,
    )
    application.run_polling(allowed_updates=["message", "callback_query", "inline_query"])


if __name__ == "__main__":
    main()
