from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from html import escape
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlunsplit

import httpx

_TZ_CN = timezone(timedelta(hours=8))
_FILETIME_UNIX_EPOCH = 11644473600

# 用户原文里的扩展条件，点格式按钮时去掉后再追加 ext:
_EXT_FILTER_RE = re.compile(
    r"(?i)(?:^|\s)(?:ext:[^\s]+|\*\.[A-Za-z0-9]+|file:[^\s]+)"
)


class SearchError(Exception):
    pass


@dataclass(frozen=True)
class FileHit:
    name: str
    path: str
    size: int
    date_modified: str
    item_type: str

    @property
    def ext(self) -> str:
        if "." not in self.name:
            return ""
        return self.name.rsplit(".", 1)[-1].lower()

    @property
    def path_tail(self) -> str:
        return self.display_path.rstrip("/").split("/")[-1]

    @property
    def display_path(self) -> str:
        raw = (self.path or "").strip()
        if not raw:
            return ""
        parsed = urlparse(raw)
        if parsed.scheme and parsed.path:
            return unquote(parsed.path)
        return unquote(raw)

    def _absolute_url(self, path: str) -> str:
        parsed = urlparse(self.path)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        if not path.startswith("/"):
            path = "/" + path
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                quote(path, safe="/"),
                parsed.query,
                parsed.fragment,
            )
        )

    @property
    def folder_url(self) -> str:
        """站点上的目录地址。"""
        parsed = urlparse(self.path)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return self._absolute_url(unquote(parsed.path or ""))

    @property
    def browse_url(self) -> str:
        """站点上该文件的浏览地址（不含 /d/ 直链前缀）。"""
        parsed = urlparse(self.path)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        dir_path = unquote(parsed.path or "").rstrip("/")
        name = (self.name or "").strip()
        if not name:
            return self._absolute_url(dir_path)
        file_path = f"{dir_path}/{name}" if dir_path else f"/{name}"
        return self._absolute_url(file_path)

    @property
    def download_url(self) -> str:
        parsed = urlparse(self.path)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            dir_path = unquote(parsed.path or "").rstrip("/")
            file_path = f"{dir_path}/{self.name}" if dir_path else f"/{self.name}"
            joined = urlunsplit((parsed.scheme, parsed.netloc, file_path, "", ""))
            return to_direct_download_url(joined)
        return urljoin(self.path.rstrip("/") + "/", quote(self.name))

    @property
    def is_file(self) -> bool:
        return self.item_type == "file"


def to_direct_download_url(url: str) -> str:
    """AList 浏览地址返回 HTML，直链在 /d/ 前缀。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return url
    path = unquote(parsed.path or "")
    if path.startswith("/d/") or path == "/d":
        return url
    if not path.startswith("/"):
        path = "/" + path
    return urlunsplit((parsed.scheme, parsed.netloc, "/d" + path, parsed.query, parsed.fragment))


def strip_ext_filters(keyword: str) -> str:
    cleaned = _EXT_FILTER_RE.sub(" ", keyword)
    return " ".join(cleaned.split())


def build_query(keyword: str, ext: str | None) -> str:
    """透传原生语法；按钮筛选时覆盖已有 ext: / *.ext / file:。"""
    text = (keyword or "").strip()
    if not ext:
        return text
    base = strip_ext_filters(text)
    if not base:
        return f"ext:{ext}"
    return f"{base} ext:{ext}"


def format_mtime(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        n = int(float(text))
    except ValueError:
        return text
    if n <= 0:
        return ""
    if n >= 10**16:
        unix = n / 10_000_000 - _FILETIME_UNIX_EPOCH
    elif n >= 10**12:
        unix = n / 1000
    else:
        unix = n
    try:
        return datetime.fromtimestamp(unix, tz=_TZ_CN).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return text


def format_hit_meta(hit: FileHit) -> str:
    ext = hit.ext.upper() if hit.ext else "?"
    parts = [format_size(hit.size), ext]
    mtime = format_mtime(hit.date_modified)
    if mtime:
        parts.append(mtime)
    return " · ".join(parts)


def format_hit_html(
    hit: FileHit,
    *,
    index: int | None = None,
    send_url: str | None = None,
) -> str:
    title = f"<b>{escape(hit.name)}</b>"
    if index is not None:
        title = f"{index}. {title}"
    meta = escape(format_hit_meta(hit))
    actions: list[str] = []
    if send_url:
        actions.append(f'<a href="{escape(send_url)}">[发给我]</a>')
    if hit.browse_url:
        actions.append(f'<a href="{escape(hit.browse_url)}">[打开网址]</a>')
    if actions:
        meta = f"{meta} · {' · '.join(actions)}"
    lines = [title, meta]
    if hit.display_path:
        label = escape(hit.display_path)
        folder = hit.folder_url
        if folder:
            lines.append(f'└ <a href="{escape(folder)}">{label}</a>')
        else:
            lines.append(f"└ <code>{label}</code>")
    return "\n".join(lines)


def format_size(size: int) -> str:
    n = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{size} B"


def _parse_size(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _parse_hit(item: dict[str, Any]) -> FileHit:
    return FileHit(
        name=str(item.get("name") or ""),
        path=str(item.get("path") or ""),
        size=_parse_size(item.get("size")),
        date_modified=str(item.get("date_modified") or ""),
        item_type=str(item.get("type") or "file"),
    )


class EverythingClient:
    def __init__(self, base_url: str, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def search(
        self,
        query: str,
        *,
        count: int = 50,
        sort: str = "date_modified",
        ascending: int = 0,
    ) -> tuple[int, list[FileHit]]:
        params = {
            "search": query,
            "json": 1,
            "path_column": 1,
            "size_column": 1,
            "date_modified_column": 1,
            "count": count,
            "sort": sort,
            "ascending": ascending,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.get(self.base_url + "/", params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise SearchError("搜索超时，请稍后再试") from exc
        except httpx.HTTPError as exc:
            raise SearchError("搜索服务暂时不可用") from exc
        except ValueError as exc:
            raise SearchError("搜索结果解析失败") from exc

        total = int(data.get("totalResults") or 0)
        hits = [_parse_hit(item) for item in data.get("results") or []]
        files = [hit for hit in hits if hit.is_file and hit.name]
        return total, files
