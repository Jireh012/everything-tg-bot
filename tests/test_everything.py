from bot.everything import FileHit, build_query, format_size, strip_ext_filters


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
    assert hit.download_url.endswith("%E5%A4%9A%E5%85%83%E7%A4%BE%E4%BC%9A%E4%B8%AD%E7%9A%84%E5%9F%BA%E7%9D%A3%E6%95%99.pdf")
    assert hit.ext == "pdf"
    assert hit.path_tail == "新建文件夹_27e6"


if __name__ == "__main__":
    test_passthrough()
    test_button_overrides_ext()
    test_strip_ext_filters()
    test_format_size()
    test_download_url()
    print("ok")
