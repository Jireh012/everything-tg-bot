from __future__ import annotations

import logging
import math
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputFile,
    InputTextMessageContent,
    Message,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.config import Config
from bot.downloader import DownloadError, Downloader, cleanup
from bot.everything import (
    EverythingClient,
    FileHit,
    SearchError,
    build_query,
    format_hit_html,
    format_hit_meta,
    format_size,
)
from bot.formats import ALL_FORMATS, build_result_keyboard
from bot.session import InlineHitStore, RateLimiter, SessionStore, UserSession

logger = logging.getLogger(__name__)

START_TEXT = """\
用关键词搜索文件，点「发给我」或回复编号即可下载。

直接发送：
• <code>多元社会中</code>
• <code>ext:pdf 多元社会中</code>
• <code>ext:pdf;epub;mobi 福音</code>
• <code>*.epub 多元社会</code>
• <code>size:&gt;10mb ext:pdf</code>

任意对话输入 <code>{mention} 关键词</code> 可 Inline 搜索，点结果里的「私聊下载」取文件。

结果下方可点格式按钮筛选（PDF / EPUB / …），点选后会重新向网站查询。
翻页用「上一页 / 下一页」。会话约 30 分钟有效。
"""


class BotApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = EverythingClient(config.everything_base_url)
        self.sessions = SessionStore(config.session_ttl_seconds)
        self.search_limiter = RateLimiter(config.search_rate_per_minute)
        self.inline_limiter = RateLimiter(config.inline_search_rate_per_minute)
        self.download_limiter = RateLimiter(config.download_rate_per_minute)
        self.inline_hits = InlineHitStore(config.session_ttl_seconds)
        self.downloader = Downloader(
            config.download_dir,
            config.max_file_size_bytes,
            config.max_concurrent_downloads,
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        user = update.effective_user
        if not message:
            return
        args = context.args or []
        if args and user:
            payload = args[0]
            if payload.startswith("dl_"):
                hit = self.inline_hits.get(payload[3:])
                if hit is None:
                    await message.reply_text("这条结果已过期，请重新搜索后再点「发给我」。")
                    return
                await self._download_hit(message, user.id, hit)
                return
            if payload.startswith("n_"):
                try:
                    index = int(payload[2:])
                except ValueError:
                    await message.reply_text("编号无效，请重新搜索后再点「发给我」。")
                    return
                await self._download_by_index(message, user.id, index)
                return
        mention = f"@{context.bot.username}" if context.bot.username else "@bot"
        await message.reply_text(START_TEXT.format(mention=mention), parse_mode=ParseMode.HTML)

    async def search_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        user = update.effective_user
        if not message or not user:
            return
        keyword = " ".join(context.args or []).strip()
        if not keyword:
            await message.reply_text("用法：/search 关键词")
            return
        await self._search(message, user.id, keyword, ext=None, more=False)

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        user = update.effective_user
        if not message or not message.text or not user:
            return
        text = message.text.strip()
        if text.startswith("/"):
            return

        if text.isdigit():
            await self._download_by_index(message, user.id, int(text))
            return

        await self._search(message, user.id, text, ext=None, more=False)

    async def on_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.inline_query
        user = update.effective_user
        if not query or not user:
            return
        mention = f"@{context.bot.username}" if context.bot.username else "@bot"
        keyword = (query.query or "").strip()
        button = InlineQueryResultsButton(text="打开私聊", start_parameter="inline")

        if not keyword:
            await query.answer(
                results=[
                    self._inline_notice(
                        "hint",
                        "输入关键词搜索文件",
                        "例如：ext:pdf 多元社会中",
                        f"在任意对话输入 {mention} 关键词 即可搜索。",
                    )
                ],
                cache_time=10,
                is_personal=True,
                button=button,
            )
            return

        if not self.inline_limiter.allow(user.id):
            await query.answer(
                results=[
                    self._inline_notice(
                        "rate",
                        "搜索太频繁",
                        "请稍后再试",
                        "搜索太频繁，请稍后再试",
                    )
                ],
                cache_time=1,
                is_personal=True,
                button=button,
            )
            return

        try:
            offset = int(query.offset or 0)
        except ValueError:
            offset = 0

        try:
            total, results = await self.client.search(
                keyword, count=self.config.max_results
            )
        except SearchError as exc:
            await query.answer(
                results=[
                    self._inline_notice("err", "搜索失败", str(exc), str(exc))
                ],
                cache_time=1,
                is_personal=True,
                button=button,
            )
            return

        page_size = min(self.config.page_size, 50)
        chunk = results[offset : offset + page_size]
        next_offset = (
            str(offset + page_size) if offset + page_size < len(results) else ""
        )
        articles: list[InlineQueryResultArticle] = [
            self._inline_article(hit, context.bot.username, total=total)
            for hit in chunk
        ]
        if not articles:
            articles.append(
                self._inline_notice(
                    "empty",
                    "没有找到文件",
                    "换个关键词或格式试试",
                    f"没有找到：{keyword}",
                )
            )
        await query.answer(
            results=articles,
            cache_time=15,
            is_personal=True,
            next_offset=next_offset,
            button=button,
        )

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user:
            return
        data = query.data or ""
        session = self.sessions.get(user.id)
        if session is None:
            await query.answer("搜索已过期，请重新发送关键词", show_alert=True)
            return

        if data.startswith("p:"):
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                await query.answer()
                return
            page_count = self._page_count(len(session.results))
            if page < 0 or page >= page_count:
                await query.answer("没有这一页")
                return
            session.page = page
            session.touch()
            await query.answer()
            await self._render_session(query.message, session, edit=True)
            return

        if data.startswith("m:"):
            session.more_formats = data.split(":", 1)[1] == "1"
            session.touch()
            await query.answer()
            await self._render_session(query.message, session, edit=True)
            return

        if data.startswith("f:"):
            ext = data.split(":", 1)[1] or None
            if ext and ext not in ALL_FORMATS:
                await query.answer("不支持的格式")
                return
            await query.answer("正在按格式重新搜索…")
            await self._search(
                query.message,
                user.id,
                session.keyword,
                ext=ext,
                more=session.more_formats,
                edit=True,
            )
            return

        await query.answer()

    async def _search(
        self,
        message: Message,
        user_id: int,
        keyword: str,
        *,
        ext: str | None,
        more: bool,
        edit: bool = False,
    ) -> None:
        if not self.search_limiter.allow(user_id):
            await message.reply_text("搜索太频繁，请稍后再试")
            return

        query = build_query(keyword, ext)
        if not query:
            await message.reply_text("请输入搜索关键词")
            return

        waiting = None
        if edit:
            try:
                await message.edit_text("搜索中…")
            except TelegramError:
                pass
        else:
            waiting = await message.reply_text("搜索中…")

        try:
            total, results = await self.client.search(
                query, count=self.config.max_results
            )
        except SearchError as exc:
            target = waiting or message
            existing = self.sessions.get(user_id)
            if existing is not None and (edit or waiting is not None):
                await self._render_session(target, existing, edit=True)
                await message.reply_text(str(exc))
            else:
                await self._replace_or_reply(
                    target, str(exc), edit=edit or waiting is not None
                )
            return

        session = self.sessions.put(
            user_id,
            UserSession(
                keyword=keyword,
                ext=ext,
                results=results,
                total=total,
                page=0,
                more_formats=more,
            ),
        )
        target = waiting or message
        await self._render_session(target, session, edit=edit or waiting is not None)

    async def _download_by_index(self, message: Message, user_id: int, index: int) -> None:
        session = self.sessions.get(user_id)
        if session is None:
            await message.reply_text("没有可下载的结果，请先发送关键词搜索")
            return
        if index < 1 or index > len(session.results):
            await message.reply_text(f"编号无效，请回复 1–{len(session.results)}")
            return

        hit = session.results[index - 1]
        await self._download_hit(message, user_id, hit)

    async def _download_hit(self, message: Message, user_id: int, hit: FileHit) -> None:
        if not self.download_limiter.allow(user_id):
            await message.reply_text("下载太频繁，请稍后再试")
            return
        if hit.size and hit.size > self.config.max_file_size_bytes:
            await message.reply_text(
                f"文件过大（{format_size(hit.size)}），上限 "
                f"{format_size(self.config.max_file_size_bytes)}"
            )
            return

        status = await message.reply_text(f"准备下载：{hit.name}")
        await message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
        dest: Path | None = None

        async def on_progress(written: int, total: int | None) -> None:
            if total:
                pct = min(99, int(written * 100 / total))
                text = f"下载中 {pct}%（{format_size(written)} / {format_size(total)}）\n{hit.name}"
            else:
                text = f"下载中 {format_size(written)}\n{hit.name}"
            try:
                await status.edit_text(text)
            except TelegramError:
                pass

        try:
            dest = await self.downloader.download(
                hit.download_url,
                hit.name,
                expected_size=hit.size,
                on_progress=on_progress,
            )
            try:
                await status.edit_text(f"正在发送：{hit.name}")
            except TelegramError:
                pass
            await message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
            await self._send_document(message, dest, hit)
            try:
                await status.delete()
            except TelegramError:
                pass
        except DownloadError as exc:
            try:
                await status.edit_text(str(exc))
            except TelegramError:
                await message.reply_text(str(exc))
        except TelegramError:
            logger.exception("发送文件失败")
            try:
                await status.edit_text("发送失败，请稍后再试")
            except TelegramError:
                pass
        finally:
            cleanup(dest)

    async def _send_document(self, message: Message, dest: Path, hit: FileHit) -> None:
        timeout = {"read_timeout": 600, "write_timeout": 600, "connect_timeout": 30}
        if self.config.use_file_uri:
            await message.reply_document(
                document=f"file://{dest.resolve()}",
                filename=hit.name,
                caption=hit.name,
                **timeout,
            )
            return
        with dest.open("rb") as fh:
            await message.reply_document(
                document=InputFile(fh, filename=hit.name),
                caption=hit.name,
                **timeout,
            )

    def _page_count(self, n: int) -> int:
        if n <= 0:
            return 1
        return max(1, math.ceil(n / self.config.page_size))

    async def _render_session(
        self, message: Message, session: UserSession, *, edit: bool
    ) -> None:
        bot = message.get_bot()
        text = self._format_list(
            session, bot_username=bot.username if bot else None
        )
        markup = build_result_keyboard(
            page=session.page,
            page_count=self._page_count(len(session.results)),
            current_ext=session.ext,
            more=session.more_formats,
        )
        if edit:
            try:
                sent = await message.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramError:
                sent = await message.reply_text(
                    text,
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
        else:
            sent = await message.reply_text(
                text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        session.result_chat_id = sent.chat_id
        session.result_message_id = sent.message_id
        session.touch()

    def _format_list(
        self, session: UserSession, *, bot_username: str | None = None
    ) -> str:
        shown = len(session.results)
        page_count = self._page_count(shown)
        page = min(session.page, page_count - 1)
        session.page = page
        fmt = session.ext.upper() if session.ext else "全部"
        header = f"共 {session.total} 条"
        if session.total > shown:
            header += f"，显示前 {shown} 条"
        header += f"，第 {page + 1}/{page_count} 页 · 格式：{fmt}"

        if not session.results:
            return header + "\n\n没有找到文件。换个关键词或格式试试。"

        start = page * self.config.page_size
        chunk = session.results[start : start + self.config.page_size]
        blocks = [header]
        for offset, hit in enumerate(chunk, start=start + 1):
            send_url = (
                f"https://t.me/{bot_username}?start=n_{offset}"
                if bot_username
                else None
            )
            blocks.append(format_hit_html(hit, index=offset, send_url=send_url))
        return "\n\n".join(blocks)

    def _inline_article(
        self, hit: FileHit, bot_username: str | None, *, total: int
    ) -> InlineQueryResultArticle:
        token = self.inline_hits.put(hit)
        description = format_hit_meta(hit)
        text = format_hit_html(hit)
        text += f"\n\n共 {total} 条，可打开站点或点「私聊下载」取文件。"
        markup = None
        actions: list[InlineKeyboardButton] = []
        if hit.browse_url:
            actions.append(InlineKeyboardButton("打开站点", url=hit.browse_url))
        if bot_username:
            actions.append(
                InlineKeyboardButton(
                    "私聊下载",
                    url=f"https://t.me/{bot_username}?start=dl_{token}",
                )
            )
        if actions:
            markup = InlineKeyboardMarkup([actions])
        return InlineQueryResultArticle(
            id=token,
            title=hit.name,
            description=description,
            input_message_content=InputTextMessageContent(
                text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            ),
            reply_markup=markup,
        )

    @staticmethod
    def _inline_notice(
        result_id: str, title: str, description: str, message: str
    ) -> InlineQueryResultArticle:
        return InlineQueryResultArticle(
            id=result_id,
            title=title,
            description=description,
            input_message_content=InputTextMessageContent(message),
        )

    async def _replace_or_reply(self, message: Message, text: str, *, edit: bool) -> None:
        if edit:
            try:
                await message.edit_text(text)
                return
            except TelegramError:
                pass
        await message.reply_text(text)
