from __future__ import annotations

import logging
import math
from pathlib import Path

from telegram import InputFile, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.config import Config
from bot.downloader import DownloadError, Downloader, cleanup
from bot.everything import EverythingClient, FileHit, SearchError, build_query, format_size
from bot.formats import ALL_FORMATS, build_result_keyboard
from bot.session import RateLimiter, SessionStore, UserSession

logger = logging.getLogger(__name__)

START_TEXT = """\
用关键词搜索文件，回复编号即可下载。

直接发送：
• <code>多元社会中</code>
• <code>ext:pdf 多元社会中</code>
• <code>ext:pdf;epub;mobi 福音</code>
• <code>*.epub 多元社会</code>
• <code>size:&gt;10mb ext:pdf</code>

结果下方可点格式按钮筛选（PDF / EPUB / …），点选后会重新向网站查询。
翻页用「上一页 / 下一页」。会话约 30 分钟有效。
"""


class BotApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = EverythingClient(config.everything_base_url)
        self.sessions = SessionStore(config.session_ttl_seconds)
        self.search_limiter = RateLimiter(config.search_rate_per_minute)
        self.download_limiter = RateLimiter(config.download_rate_per_minute)
        self.downloader = Downloader(
            config.download_dir,
            config.max_file_size_bytes,
            config.max_concurrent_downloads,
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message:
            await update.message.reply_text(START_TEXT, parse_mode=ParseMode.HTML)

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
        if not self.download_limiter.allow(user_id):
            await message.reply_text("下载太频繁，请稍后再试")
            return

        hit = session.results[index - 1]
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
        text = self._format_list(session)
        markup = build_result_keyboard(
            page=session.page,
            page_count=self._page_count(len(session.results)),
            current_ext=session.ext,
            more=session.more_formats,
        )
        if edit:
            try:
                sent = await message.edit_text(text, reply_markup=markup)
            except TelegramError:
                sent = await message.reply_text(text, reply_markup=markup)
        else:
            sent = await message.reply_text(text, reply_markup=markup)
        session.result_chat_id = sent.chat_id
        session.result_message_id = sent.message_id
        session.touch()

    def _format_list(self, session: UserSession) -> str:
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
        lines = [header, ""]
        for offset, hit in enumerate(chunk, start=start + 1):
            ext = hit.ext.upper() if hit.ext else "?"
            line = f"{offset}. {hit.name}  ({format_size(hit.size)} · {ext})"
            if hit.path_tail:
                line += f"\n    └ {hit.path_tail}"
            lines.append(line)
        lines.append("")
        lines.append("回复编号下载，例如：1")
        return "\n".join(lines)

    async def _replace_or_reply(self, message: Message, text: str, *, edit: bool) -> None:
        if edit:
            try:
                await message.edit_text(text)
                return
            except TelegramError:
                pass
        await message.reply_text(text)
