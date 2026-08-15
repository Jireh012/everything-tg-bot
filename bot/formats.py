from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PRIMARY_FORMATS = ("pdf", "epub", "mobi", "azw3", "djvu", "txt")
SECONDARY_FORMATS = ("doc", "docx", "zip", "rar", "7z")
MORE_FORMATS = ("azw", "fb2", "mp3", "mp4", "mkv")

ALL_FORMATS = PRIMARY_FORMATS + SECONDARY_FORMATS + MORE_FORMATS


def _label(ext: str | None, current: str | None) -> str:
    if ext is None:
        text = "全部"
        return f"✓ {text}" if current is None else text
    text = ext.upper()
    return f"✓ {text}" if current == ext else text


def build_result_keyboard(
    *,
    page: int,
    page_count: int,
    current_ext: str | None,
    more: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(_label(None, current_ext), callback_data="f:"),
            *[
                InlineKeyboardButton(_label(ext, current_ext), callback_data=f"f:{ext}")
                for ext in PRIMARY_FORMATS
            ],
        ],
        [
            *[
                InlineKeyboardButton(_label(ext, current_ext), callback_data=f"f:{ext}")
                for ext in SECONDARY_FORMATS
            ],
            InlineKeyboardButton(
                "收起" if more else "更多",
                callback_data="m:0" if more else "m:1",
            ),
        ],
    ]
    if more:
        rows.append(
            [
                InlineKeyboardButton(_label(ext, current_ext), callback_data=f"f:{ext}")
                for ext in MORE_FORMATS
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("上一页", callback_data=f"p:{page - 1}"))
    if page + 1 < page_count:
        nav.append(InlineKeyboardButton("下一页", callback_data=f"p:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)
