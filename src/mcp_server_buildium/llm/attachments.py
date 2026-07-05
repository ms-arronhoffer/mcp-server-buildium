"""Document attachments for the server-side assistant.

A user chat message may carry one or more *attachments* — documents the user
uploaded (e.g. a lease PDF, a scanned image, a DOCX) that the assistant reads to
extract fields and create Buildium objects. This module keeps the attachment
model provider-neutral:

* :class:`Attachment` is the normalized in-memory representation.
* :func:`normalize_attachments` validates raw client payloads (media-type
  allow-list, size caps, base64 decodability) and raises :class:`AttachmentError`
  on the first problem so the ``/chat`` route can answer ``400``.
* :func:`extract_text` provides a best-effort text fallback for providers that
  cannot natively accept a given document type.
* A :class:`contextvars.ContextVar` (:data:`current_attachments`) exposes the
  active request's attachments to in-process MCP tools (e.g. so a tool can save
  the uploaded bytes to Buildium) without threading them through the model.

The raw bytes never leave the server except when explicitly uploaded to Buildium
on the user's behalf.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
from dataclasses import dataclass, field
from typing import Any

# Media types the assistant accepts. Images and PDFs are handled natively by the
# multimodal providers; DOCX and plain text are extracted to text server-side
# (see :func:`extract_text`) because no provider ingests them natively.
IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
PDF_MEDIA_TYPE = "application/pdf"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/csv", "text/markdown"})

ALLOWED_MEDIA_TYPES = frozenset(
    {PDF_MEDIA_TYPE, DOCX_MEDIA_TYPE, *IMAGE_MEDIA_TYPES, *TEXT_MEDIA_TYPES}
)

# One megabyte in bytes; size caps are expressed in MB in configuration.
_BYTES_PER_MB = 1024 * 1024

# Upper bound on the *decompressed* size of an entry read out of a DOCX (ZIP)
# container. A DOCX is a ZIP archive, and ``zipfile`` decompresses on read with
# no size limit, so a small "zip bomb" could otherwise expand to gigabytes and
# exhaust memory (CWE-409). We refuse to read entries larger than this.
_MAX_DOCX_ENTRY_BYTES = 50 * _BYTES_PER_MB


class AttachmentError(ValueError):
    """Raised when a client-supplied attachment is malformed or not permitted."""


@dataclass
class Attachment:
    """A normalized, validated document attached to a chat message.

    ``data`` is the raw (already base64-decoded) file bytes; ``data_b64`` is the
    original base64 text, retained so provider adapters can forward it without
    re-encoding.
    """

    name: str
    media_type: str
    data: bytes
    data_b64: str = field(repr=False)

    @property
    def size(self) -> int:
        return len(self.data)

    def is_image(self) -> bool:
        return self.media_type in IMAGE_MEDIA_TYPES

    def is_pdf(self) -> bool:
        return self.media_type == PDF_MEDIA_TYPE


def normalize_attachments(
    raw: Any,
    *,
    max_bytes: int,
    max_count: int,
) -> list[Attachment]:
    """Validate and decode a client-supplied list of attachments.

    Args:
        raw: The ``attachments`` value from a user message. Expected to be a list
            of ``{"name", "media_type", "data"}`` objects where ``data`` is
            base64-encoded file bytes.
        max_bytes: Maximum permitted size of a single decoded attachment.
        max_count: Maximum number of attachments accepted in one request.

    Returns:
        The normalized attachments (empty list when ``raw`` is falsy).

    Raises:
        AttachmentError: On any structural, media-type, size, or base64 problem.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise AttachmentError("'attachments' must be a list of objects.")
    if len(raw) > max_count:
        raise AttachmentError(
            f"Too many attachments: {len(raw)} (maximum {max_count} per request)."
        )

    out: list[Attachment] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise AttachmentError(f"Attachment #{index + 1} must be an object.")
        media_type = str(item.get("media_type") or item.get("mediaType") or "").strip().lower()
        # Some providers/clients append parameters (e.g. "text/plain; charset=utf-8").
        media_type = media_type.split(";", 1)[0].strip()
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise AttachmentError(
                f"Attachment #{index + 1} has an unsupported type {media_type!r}. "
                f"Allowed types: {', '.join(sorted(ALLOWED_MEDIA_TYPES))}."
            )
        name = str(item.get("name") or item.get("filename") or f"attachment-{index + 1}").strip()
        raw_data = item.get("data")
        if not isinstance(raw_data, str) or not raw_data.strip():
            raise AttachmentError(f"Attachment #{index + 1} is missing base64 'data'.")
        data_b64 = _strip_data_url(raw_data.strip())
        try:
            decoded = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError(
                f"Attachment #{index + 1} ({name!r}) is not valid base64."
            ) from exc
        if len(decoded) == 0:
            raise AttachmentError(f"Attachment #{index + 1} ({name!r}) is empty.")
        if len(decoded) > max_bytes:
            raise AttachmentError(
                f"Attachment #{index + 1} ({name!r}) is too large "
                f"({len(decoded)} bytes; maximum {max_bytes})."
            )
        out.append(Attachment(name=name, media_type=media_type, data=decoded, data_b64=data_b64))
    return out


def _strip_data_url(value: str) -> str:
    """Return the base64 payload of a ``data:`` URL, or ``value`` unchanged."""
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def mb_to_bytes(mb: int) -> int:
    """Convert a megabyte size cap to bytes."""
    return int(mb) * _BYTES_PER_MB


def extract_text(attachment: Attachment) -> str | None:
    """Best-effort extraction of an attachment's text content.

    Used as a fallback for providers that cannot natively ingest a document type
    (e.g. DOCX, or PDFs on a provider without file support). Returns ``None`` when
    the type is not text-extractable here (images), so the caller can decide how
    to inform the model.
    """
    if attachment.media_type in TEXT_MEDIA_TYPES:
        return attachment.data.decode("utf-8", errors="replace")
    if attachment.media_type == DOCX_MEDIA_TYPE:
        return _extract_docx_text(attachment.data)
    if attachment.is_pdf():
        return _extract_pdf_text(attachment.data)
    return None


def _extract_docx_text(data: bytes) -> str | None:
    """Extract visible text from a DOCX (Office Open XML) document.

    Reads ``word/document.xml`` from the zip container and concatenates the text
    runs. Uses only the standard library so no new dependency is required.
    Returns ``None`` if the archive cannot be parsed as a DOCX.
    """
    import io
    import re
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            try:
                info = zf.getinfo("word/document.xml")
            except KeyError:
                return None
            # Guard against decompression bombs: reject entries whose declared
            # uncompressed size exceeds our cap before decompressing them.
            if info.file_size > _MAX_DOCX_ENTRY_BYTES:
                return None
            # Read one byte past the cap so a truncated/lying header (declared
            # size small but actual content large) is still detected as oversize.
            with zf.open(info) as fh:
                raw = fh.read(_MAX_DOCX_ENTRY_BYTES + 1)
            if len(raw) > _MAX_DOCX_ENTRY_BYTES:
                return None
            xml = raw.decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    # Convert paragraph and break boundaries to newlines before stripping tags so
    # the extracted text keeps a usable line structure for the model.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:br\s*/>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = _unescape_xml(text)
    return text.strip() or None


def _extract_pdf_text(data: bytes) -> str | None:
    """Extract text from a PDF if a PDF library is available, else ``None``.

    PDF text extraction requires a third-party parser. If none is installed the
    function returns ``None`` and the caller falls back to telling the model the
    document could not be read as text (native multimodal providers still receive
    the raw PDF).
    """
    try:  # pragma: no cover - optional dependency, not tested
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    import io

    try:  # pragma: no cover - depends on optional dependency
        reader = PdfReader(io.BytesIO(data))
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return None
    text = "\n".join(parts).strip()
    return text or None


def _unescape_xml(text: str) -> str:
    """Unescape the small set of XML entities produced in DOCX text."""
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


# In-process, per-request registry of the current chat turn's attachments, keyed
# by file name. Set by the /chat route and read by MCP tools that need the raw
# bytes (e.g. to upload the document to Buildium). Reset after each request.
current_attachments: contextvars.ContextVar[dict[str, Attachment]] = contextvars.ContextVar(
    "current_attachments", default={}
)


def set_current_attachments(attachments: list[Attachment]) -> contextvars.Token:
    """Publish ``attachments`` for the active request; returns a reset token."""
    return current_attachments.set({a.name: a for a in attachments})


def get_current_attachment(name: str) -> Attachment | None:
    """Return the current request's attachment with ``name``, if any.

    Falls back to a case-insensitive match so the model can reference a file by
    the name it was shown even if casing differs.
    """
    registry = current_attachments.get()
    if name in registry:
        return registry[name]
    lowered = name.strip().lower()
    for key, value in registry.items():
        if key.lower() == lowered:
            return value
    return None


def list_current_attachment_names() -> list[str]:
    """Return the names of the current request's attachments."""
    return list(current_attachments.get().keys())
