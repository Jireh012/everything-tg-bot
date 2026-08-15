from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from bot.everything import format_size, to_direct_download_url

ProgressCallback = Callable[[int, int | None], Awaitable[None]]

_UNSAFE_NAME = re.compile(r"[^\w.\u4e00-\u9fff\-()\[\] ]+", re.UNICODE)


class DownloadError(Exception):
    pass


def safe_filename(name: str) -> str:
    cleaned = _UNSAFE_NAME.sub("_", name).strip(" ._")
    return (cleaned or "file")[:180]


class Downloader:
    def __init__(
        self,
        download_dir: str,
        max_file_size: int,
        max_concurrent: int,
    ) -> None:
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.max_file_size = max_file_size
        self._sema = asyncio.Semaphore(max_concurrent)

    async def download(
        self,
        url: str,
        filename: str,
        expected_size: int = 0,
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        async with self._sema:
            return await self._download(url, filename, expected_size, on_progress)

    async def _stream_to_file(
        self,
        client: httpx.AsyncClient,
        url: str,
        dest: Path,
        expected_size: int,
        on_progress: ProgressCallback | None,
    ) -> int:
        written = 0
        last_report = 0.0
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                raise DownloadError(f"下载失败（HTTP {resp.status_code}）")
            total = _content_length(resp) or (expected_size or None)
            if total and total > self.max_file_size:
                raise DownloadError(
                    f"文件过大（{format_size(total)}），上限 {format_size(self.max_file_size)}"
                )
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(64 * 1024):
                    written += len(chunk)
                    if written > self.max_file_size:
                        raise DownloadError(
                            f"文件过大，上限 {format_size(self.max_file_size)}"
                        )
                    fh.write(chunk)
                    now = asyncio.get_running_loop().time()
                    if on_progress and now - last_report >= 2.0:
                        last_report = now
                        await on_progress(written, total)
        return written

    async def _download(
        self,
        url: str,
        filename: str,
        expected_size: int,
        on_progress: ProgressCallback | None,
    ) -> Path:
        work_dir = self.download_dir / uuid.uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)
        dest = work_dir / safe_filename(filename)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        }
        timeout = httpx.Timeout(30.0, read=600.0)

        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True, headers=headers
            ) as client:
                written = await self._stream_to_file(
                    client, url, dest, expected_size, on_progress
                )
                if _file_looks_like_html(dest):
                    alt = to_direct_download_url(url)
                    if alt != url:
                        _unlink_quiet(dest)
                        written = await self._stream_to_file(
                            client, alt, dest, expected_size, on_progress
                        )
                if written == 0:
                    raise DownloadError("下载内容为空")
                if _file_looks_like_html(dest):
                    raise DownloadError("下载到的是网页而不是文件，请稍后重试")
            if on_progress:
                await on_progress(written, written)
            return dest
        except httpx.TimeoutException as exc:
            cleanup(dest)
            raise DownloadError("下载超时") from exc
        except httpx.HTTPError as exc:
            cleanup(dest)
            raise DownloadError("下载连接失败") from exc
        except DownloadError:
            cleanup(dest)
            raise
        except OSError as exc:
            cleanup(dest)
            raise DownloadError("写入临时文件失败") from exc


_UUID_DIR = re.compile(r"^[0-9a-f]{32}$")


def cleanup(path: Path | None) -> None:
    if path is None:
        return
    parent = path.parent
    _unlink_quiet(path)
    if _UUID_DIR.fullmatch(parent.name):
        try:
            parent.rmdir()
        except OSError:
            pass


def _looks_like_html(content_type: str | None, sample: bytes) -> bool:
    ctype = (content_type or "").lower()
    if "text/html" in ctype:
        return True
    head = sample.lstrip()[:64].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _file_looks_like_html(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        sample = path.read_bytes()[:256]
    except OSError:
        return False
    return _looks_like_html(None, sample)


def _content_length(resp: httpx.Response) -> int | None:
    raw = resp.headers.get("Content-Length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _unlink_quiet(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
