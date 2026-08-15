from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin

import httpx

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
        return self.path.rstrip("/").split("/")[-1]

    @property
    def download_url(self) -> str:
        base = self.path.rstrip("/") + "/"
        return urljoin(base, quote(self.name))

    @property
    def is_file(self) -> bool:
        return self.item_type == "file"


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
