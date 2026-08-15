from datetime import datetime, timezone, timedelta

from bot.everything import (
    FileHit,
    build_query,
    format_hit_html,
    format_hit_meta,
    format_mtime,
    format_size,
    strip_ext_filters,
    to_direct_download_url,
)

_TZ_CN = timezone(timedelta(hours=8))
from bot.downloader import cleanup, safe_filename
from bot.session import InlineHitStore


def test_passthrough() -> None:
    assert build_query("ext:pdf 多元社会中", None) == "ext:pdf 多元社会中"
    assert build_query("*.epub 福音", None) == "*.epub 福音"
    assert build_query("size:>10mb ext:pdf", None) == "size:>10mb ext:pdf"


def test_button_overrides_ext() -> None:
    assert build_query("多元社会中", "pdf") == "多元社会中 ext:pdf"
    assert build_query("ext:pdf 多元社会中", "epub") == "多元社会中 ext:epub"
    assert build_query("*.pdf 多元社会", "epub") == "多元社会 ext:epub"
    assert build_query("size:>10mb ext:pdf", "epub") == "size:>10mb ext:epub"
    assert build_query("ext:pdf", "txt") == "ext:txt"


def test_strip_ext_filters() -> None:
    assert strip_ext_filters("ext:pdf;epub 福音") == "福音"
    assert strip_ext_filters("file:foo.pdf bar") == "bar"


def test_format_size() -> None:
    assert format_size(1621887) == "1.5 MB"
    assert format_size(512) == "512 B"


def test_download_url() -> None:
    hit = FileHit(
        name="多元社会中的基督教.pdf",
        path="http://www.https.ng/baidupan/txt/09_其他综合资源/新建文件夹_27e6",
        size=1621887,
        date_modified="",
        item_type="file",
    )
    assert (
        hit.download_url
        == "http://www.https.ng/d/baidupan/txt/09_其他综合资源/新建文件夹_27e6/多元社会中的基督教.pdf"
    )
    assert hit.ext == "pdf"
    assert hit.path_tail == "新建文件夹_27e6"
    assert hit.display_path == "/baidupan/txt/09_其他综合资源/新建文件夹_27e6"


def test_to_direct_download_url() -> None:
    src = "http://www.https.ng/baidupan/txt/a.pdf"
    assert to_direct_download_url(src) == "http://www.https.ng/d/baidupan/txt/a.pdf"
    already = "http://www.https.ng/d/baidupan/txt/a.pdf"
    assert to_direct_download_url(already) == already


def test_format_mtime() -> None:
    assert format_mtime("") == ""
    assert format_mtime("2024-03-15 12:30:00") == "2024-03-15 12:30:00"
    unix = int(datetime(2024, 3, 15, 12, 30, tzinfo=_TZ_CN).timestamp())
    assert format_mtime(str(unix)) == "2024-03-15 12:30"
    filetime = str(int((unix + 11644473600) * 10_000_000))
    assert format_mtime(filetime) == "2024-03-15 12:30"


def test_format_hit_meta() -> None:
    unix = int(datetime(2024, 3, 15, 12, 30, tzinfo=_TZ_CN).timestamp())
    hit = FileHit(
        name="a.pdf",
        path="http://example/dir/sub",
        size=1621887,
        date_modified=str(unix),
        item_type="file",
    )
    assert format_hit_meta(hit) == "1.5 MB · PDF · 2024-03-15 12:30"
    assert format_hit_html(hit, index=1) == (
        "1. <b>a.pdf</b>\n"
        "1.5 MB · PDF · 2024-03-15 12:30\n"
        "└ <code>/dir/sub</code>"
    )


def test_inline_hit_store() -> None:
    hit = FileHit(
        name="a.pdf",
        path="http://example/dir",
        size=10,
        date_modified="",
        item_type="file",
    )
    store = InlineHitStore(ttl_seconds=60)
    token = store.put(hit)
    assert store.get(token) == hit
    assert store.get("missing") is None
    expired = InlineHitStore(ttl_seconds=60)
    token = expired.put(hit)
    ts, stored = expired._hits[token]
    expired._hits[token] = (ts - 61, stored)
    assert expired.get(token) is None


def test_safe_filename_keeps_original() -> None:
    assert safe_filename("多元社会中的基督教.pdf") == "多元社会中的基督教.pdf"


def test_cleanup_removes_uuid_dir() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        work = root / ("a" * 32)
        dest = work / "多元社会中的基督教.pdf"
        work.mkdir()
        dest.write_bytes(b"x")
        cleanup(dest)
        assert not dest.exists()
        assert not work.exists()


if __name__ == "__main__":
    test_passthrough()
    test_button_overrides_ext()
    test_strip_ext_filters()
    test_format_size()
    test_download_url()
    test_to_direct_download_url()
    test_format_mtime()
    test_format_hit_meta()
    test_inline_hit_store()
    test_safe_filename_keeps_original()
    test_cleanup_removes_uuid_dir()
    print("ok")
