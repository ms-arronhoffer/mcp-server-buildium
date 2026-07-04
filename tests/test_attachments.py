"""Tests for document attachment normalization, extraction, and the tool context."""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from mcp_server_buildium.llm.attachments import (
    Attachment,
    AttachmentError,
    current_attachments,
    extract_text,
    get_current_attachment,
    list_current_attachment_names,
    mb_to_bytes,
    normalize_attachments,
    set_current_attachments,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _docx_bytes(text: str) -> bytes:
    """Build a minimal DOCX (zip with word/document.xml) containing ``text``."""
    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_normalize_accepts_supported_types() -> None:
    raw = [
        {"name": "note.txt", "media_type": "text/plain", "data": _b64(b"hello")},
        {"name": "pic.png", "media_type": "image/png", "data": _b64(b"\x89PNG")},
    ]
    atts = normalize_attachments(raw, max_bytes=mb_to_bytes(10), max_count=5)
    assert [a.name for a in atts] == ["note.txt", "pic.png"]
    assert atts[0].data == b"hello"
    assert atts[1].is_image()


def test_normalize_empty_returns_empty_list() -> None:
    assert normalize_attachments(None, max_bytes=100, max_count=5) == []
    assert normalize_attachments([], max_bytes=100, max_count=5) == []


def test_normalize_rejects_unsupported_type() -> None:
    raw = [{"name": "app.exe", "media_type": "application/octet-stream", "data": _b64(b"x")}]
    with pytest.raises(AttachmentError, match="unsupported type"):
        normalize_attachments(raw, max_bytes=100, max_count=5)


def test_normalize_rejects_oversize() -> None:
    raw = [{"name": "big.txt", "media_type": "text/plain", "data": _b64(b"x" * 50)}]
    with pytest.raises(AttachmentError, match="too large"):
        normalize_attachments(raw, max_bytes=10, max_count=5)


def test_normalize_rejects_too_many() -> None:
    raw = [{"name": f"f{i}.txt", "media_type": "text/plain", "data": _b64(b"x")} for i in range(3)]
    with pytest.raises(AttachmentError, match="Too many attachments"):
        normalize_attachments(raw, max_bytes=100, max_count=2)


def test_normalize_rejects_bad_base64() -> None:
    raw = [{"name": "f.txt", "media_type": "text/plain", "data": "not!base64!"}]
    with pytest.raises(AttachmentError, match="not valid base64"):
        normalize_attachments(raw, max_bytes=100, max_count=5)


def test_normalize_rejects_missing_data() -> None:
    raw = [{"name": "f.txt", "media_type": "text/plain"}]
    with pytest.raises(AttachmentError, match="missing base64"):
        normalize_attachments(raw, max_bytes=100, max_count=5)


def test_normalize_strips_data_url_prefix_and_media_params() -> None:
    raw = [
        {
            "name": "f.txt",
            "media_type": "text/plain; charset=utf-8",
            "data": "data:text/plain;base64," + _b64(b"hi"),
        }
    ]
    atts = normalize_attachments(raw, max_bytes=100, max_count=5)
    assert atts[0].media_type == "text/plain"
    assert atts[0].data == b"hi"


def test_extract_text_plain() -> None:
    att = Attachment("n.txt", "text/plain", b"line one\nline two", _b64(b"x"))
    assert extract_text(att) == "line one\nline two"


def test_extract_text_docx() -> None:
    data = _docx_bytes("Lease Agreement")
    att = Attachment(
        "lease.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data,
        _b64(data),
    )
    assert extract_text(att) == "Lease Agreement"


def test_extract_text_image_returns_none() -> None:
    att = Attachment("p.png", "image/png", b"\x89PNG", _b64(b"x"))
    assert extract_text(att) is None


def test_current_attachments_contextvar_roundtrip() -> None:
    att = Attachment("lease.pdf", "application/pdf", b"%PDF", _b64(b"x"))
    token = set_current_attachments([att])
    try:
        assert list_current_attachment_names() == ["lease.pdf"]
        assert get_current_attachment("lease.pdf") is att
        # Case-insensitive lookup.
        assert get_current_attachment("LEASE.PDF") is att
        assert get_current_attachment("missing.pdf") is None
    finally:
        current_attachments.reset(token)
    assert list_current_attachment_names() == []
