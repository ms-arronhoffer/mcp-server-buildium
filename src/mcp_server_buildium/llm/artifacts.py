"""Assistant-generated downloadable files (outbound artifacts).

This module is the *outbound* counterpart to :mod:`mcp_server_buildium.llm.attachments`.
Where attachments are documents the user uploads for the assistant to read, an
*artifact* is a file the assistant generates on the user's behalf — a CSV of
active leases, a spreadsheet of properties, a slide deck of the top units — for
the user to download and save locally.

Design goals:

* **No new dependencies.** Every file format is produced with the Python
  standard library only, matching the repository's minimal-dependency ethos
  (the same reason DOCX *reading* in ``attachments.py`` is hand-rolled with
  ``zipfile``). CSV uses :mod:`csv`; XLSX/DOCX/PPTX are Office Open XML ZIP
  packages assembled with :mod:`zipfile`; PDF is a minimal hand-written PDF.
* **Provider-neutral.** The raw bytes never go to the model. A tool builds the
  file, registers it here, and the ``/chat`` route streams it to the browser as
  a base64 ``artifact`` event so the extension can offer a download link.
* **Per-request registry.** A :class:`contextvars.ContextVar` holds the files
  generated during the current chat turn, mirroring ``current_attachments``.
"""

from __future__ import annotations

import base64
import contextvars
import csv
import io
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# Supported output formats -> (media type, file extension).
FORMAT_MEDIA_TYPES: dict[str, tuple[str, str]] = {
    "csv": ("text/csv", "csv"),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "pdf": ("application/pdf", "pdf"),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
    ),
}

SUPPORTED_FORMATS = frozenset(FORMAT_MEDIA_TYPES)

# One megabyte in bytes; the size cap bounds the base64 payload streamed to the
# browser so a runaway generation cannot exhaust memory or the SSE channel.
_BYTES_PER_MB = 1024 * 1024
MAX_ARTIFACT_BYTES = 25 * _BYTES_PER_MB


class ArtifactError(ValueError):
    """Raised when a file cannot be generated from the supplied content."""


@dataclass
class Section:
    """A narrative section for document formats (DOCX, PDF)."""

    heading: str = ""
    body: str = ""


@dataclass
class Slide:
    """A single slide for the PPTX format."""

    title: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class GeneratedFile:
    """A file the assistant generated for the user to download.

    ``data`` is the raw file bytes; ``data_b64`` is the base64 text streamed to
    the browser (retained so the transport layer never re-encodes).
    """

    name: str
    media_type: str
    data: bytes
    data_b64: str = field(repr=False)

    @property
    def size(self) -> int:
        return len(self.data)

    def to_event(self) -> dict[str, object]:
        """Return the ``artifact`` SSE payload for this file."""
        return {
            "type": "artifact",
            "name": self.name,
            "media_type": self.media_type,
            "size": self.size,
            "data": self.data_b64,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_generated_file(
    *,
    file_format: str,
    filename: str | None = None,
    title: str | None = None,
    columns: list[str] | None = None,
    rows: list[list[object]] | None = None,
    sections: list[Section] | None = None,
    slides: list[Slide] | None = None,
) -> GeneratedFile:
    """Build a :class:`GeneratedFile` in the requested format from content.

    Args:
        file_format: One of :data:`SUPPORTED_FORMATS`.
        filename: Desired base file name (extension is normalized to match the
            format). Defaults to a title/format-derived name.
        title: Optional document/spreadsheet/deck title.
        columns: Header row for tabular formats (CSV, XLSX). Also rendered as a
            table in DOCX/PDF when present.
        rows: Data rows aligned to ``columns``.
        sections: Narrative sections for DOCX/PDF.
        slides: Slides for PPTX.

    Raises:
        ArtifactError: If the format is unsupported, the content is insufficient
            for the format, or the result exceeds :data:`MAX_ARTIFACT_BYTES`.
    """
    fmt = (file_format or "").strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ArtifactError(
            f"Unsupported format {file_format!r}. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_FORMATS))}."
        )
    media_type, extension = FORMAT_MEDIA_TYPES[fmt]
    columns = [str(c) for c in (columns or [])]
    rows = [[cell for cell in row] for row in (rows or [])]
    sections = sections or []
    slides = slides or []

    if fmt == "csv":
        data = _build_csv(columns, rows)
    elif fmt == "xlsx":
        data = _build_xlsx(title, columns, rows)
    elif fmt == "docx":
        data = _build_docx(title, sections, columns, rows)
    elif fmt == "pdf":
        data = _build_pdf(title, sections, columns, rows)
    else:  # pptx
        data = _build_pptx(title, slides, columns, rows)

    if len(data) > MAX_ARTIFACT_BYTES:
        raise ArtifactError(
            f"The generated file is too large ({len(data)} bytes; maximum {MAX_ARTIFACT_BYTES})."
        )

    name = _normalize_filename(filename, title, fmt, extension)
    return GeneratedFile(
        name=name,
        media_type=media_type,
        data=data,
        data_b64=base64.b64encode(data).decode("ascii"),
    )


def _normalize_filename(filename: str | None, title: str | None, fmt: str, extension: str) -> str:
    """Derive a safe file name ending in the format's extension."""
    base = (filename or title or f"buildium-{fmt}").strip()
    # Drop any directory separators and an existing extension, then sanitize.
    base = base.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    safe = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "_" for ch in base).strip()
    safe = safe or f"buildium-{fmt}"
    return f"{safe}.{extension}"


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
# Leading characters that spreadsheet applications (Excel, LibreOffice, Google
# Sheets) treat as the start of a formula. A cell beginning with one of these is
# neutralised by prefixing a single quote so it is rendered as literal text and
# never evaluated (CSV/formula injection, CWE-1236).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> str:
    """Return a spreadsheet-safe string for a CSV cell.

    Values that would otherwise be interpreted as a formula by a spreadsheet
    application are prefixed with a single quote so the content is treated as
    literal text (defends against CSV/formula injection).
    """
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


def _build_csv(columns: list[str], rows: list[list[object]]) -> bytes:
    """Serialize tabular content as UTF-8 CSV (with a BOM for Excel)."""
    if not columns and not rows:
        raise ArtifactError("A CSV export needs 'columns' and/or 'rows'.")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if columns:
        writer.writerow([_csv_safe(col) for col in columns])
    for row in rows:
        writer.writerow([_csv_safe(cell) for cell in row])
    # A UTF-8 BOM makes Excel open non-ASCII CSVs with the correct encoding.
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Office Open XML helpers (XLSX, DOCX, PPTX)
# ---------------------------------------------------------------------------
def _zip_package(parts: dict[str, str | bytes]) -> bytes:
    """Assemble an Office Open XML ZIP package from a name -> content map."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return buffer.getvalue()


def _column_letter(index: int) -> str:
    """Return the spreadsheet column letters for a zero-based column index."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _is_number(value: object) -> bool:
    """True when ``value`` should be written as a numeric spreadsheet cell."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        try:
            float(text)
        except ValueError:
            return False
        return True
    return False


def _build_xlsx(title: str | None, columns: list[str], rows: list[list[object]]) -> bytes:
    """Build a single-sheet XLSX workbook using inline strings (no sharedStrings)."""
    if not columns and not rows:
        raise ArtifactError("A spreadsheet export needs 'columns' and/or 'rows'.")

    table_rows: list[list[object]] = []
    if columns:
        table_rows.append(list(columns))
    table_rows.extend(rows)

    sheet_rows: list[str] = []
    for r_index, row in enumerate(table_rows, start=1):
        cells: list[str] = []
        for c_index, value in enumerate(row):
            ref = f"{_column_letter(c_index)}{r_index}"
            if value is None:
                continue
            if _is_number(value):
                cells.append(f'<c r="{ref}"><v>{escape(str(value).strip())}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(str(value))}</t></is></c>"
                )
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')

    sheet_name = escape((title or "Sheet1")[:31] or "Sheet1")
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    return _zip_package(
        {
            "[Content_Types].xml": content_types,
            "_rels/.rels": root_rels,
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": workbook_rels,
            "xl/worksheets/sheet1.xml": sheet_xml,
        }
    )


def _docx_paragraph(text: str, *, bold: bool = False, size: int | None = None) -> str:
    """Return a WordprocessingML paragraph for ``text`` (blank when empty)."""
    run_props = ""
    if bold or size:
        parts = ["<w:b/>"] if bold else []
        if size:
            parts.append(f'<w:sz w:val="{size * 2}"/>')
        run_props = f"<w:rPr>{''.join(parts)}</w:rPr>"
    if not text:
        return "<w:p/>"
    return f'<w:p><w:r>{run_props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _docx_table(columns: list[str], rows: list[list[object]]) -> str:
    """Return a bordered WordprocessingML table for tabular content."""
    border = (
        "<w:tblBorders>"
        + "".join(
            f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + "</w:tblBorders>"
    )
    tbl_pr = f'<w:tblPr><w:tblW w:w="0" w:type="auto"/>{border}</w:tblPr>'

    def _cell(value: object, *, bold: bool) -> str:
        run_props = "<w:rPr><w:b/></w:rPr>" if bold else ""
        text = "" if value is None else str(value)
        return (
            '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
            f'<w:p><w:r>{run_props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:tc>'
        )

    table_rows: list[str] = []
    if columns:
        table_rows.append("<w:tr>" + "".join(_cell(c, bold=True) for c in columns) + "</w:tr>")
    for row in rows:
        table_rows.append("<w:tr>" + "".join(_cell(c, bold=False) for c in row) + "</w:tr>")
    return f"<w:tbl>{tbl_pr}{''.join(table_rows)}</w:tbl>"


def _build_docx(
    title: str | None,
    sections: list[Section],
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    """Build a minimal DOCX (WordprocessingML) document."""
    if not (title or sections or columns or rows):
        raise ArtifactError("A document export needs a title, sections, or a table.")

    body: list[str] = []
    if title:
        body.append(_docx_paragraph(title, bold=True, size=20))
    for section in sections:
        if section.heading:
            body.append(_docx_paragraph(section.heading, bold=True, size=15))
        for line in (section.body or "").split("\n"):
            body.append(_docx_paragraph(line))
    if columns or rows:
        body.append(_docx_table(columns, rows))
    body.append("<w:sectPr/>")

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    return _zip_package(
        {
            "[Content_Types].xml": content_types,
            "_rels/.rels": root_rels,
            "word/document.xml": document,
        }
    )


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------
def _pptx_text_body(shape_id: int, name: str, title_para: str, body_paras: str) -> str:
    """Assemble a text-box shape XML fragment for a PPTX slide."""
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{escape(name)}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        "<p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>"
        f"{title_para}{body_paras}</p:txBody></p:sp>"
    )


def _pptx_paragraph(text: str, *, bullet: bool = False) -> str:
    """Return a DrawingML paragraph for slide text."""
    marker = "" if bullet else "<a:buNone/>"
    return f"<a:p><a:pPr>{marker}</a:pPr><a:r><a:t>{escape(text)}</a:t></a:r></a:p>"


def _build_pptx(
    title: str | None,
    slides: list[Slide],
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    """Build a minimal PPTX (PresentationML) slide deck."""
    deck: list[Slide] = list(slides)
    if not deck:
        # Derive slides from a title and/or a table when explicit slides are absent.
        if title:
            deck.append(Slide(title=title, bullets=[]))
        if columns or rows:
            header = " | ".join(str(c) for c in columns) if columns else ""
            bullets = [header] if header else []
            bullets.extend(" | ".join("" if c is None else str(c) for c in row) for row in rows)
            deck.append(Slide(title="Data", bullets=bullets[:40]))
    if not deck:
        raise ArtifactError("A slide deck export needs 'slides', a title, or a table.")

    slide_parts: dict[str, str] = {}
    slide_rels: dict[str, str] = {}
    presentation_slide_ids: list[str] = []
    presentation_rels: list[str] = []

    for index, slide in enumerate(deck, start=1):
        title_para = (
            "<p:sp><p:nvSpPr>"
            f'<p:cNvPr id="2" name="Title {index}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>'
            f"{_pptx_paragraph(slide.title or f'Slide {index}')}</p:txBody></p:sp>"
        )
        bullets = "".join(
            _pptx_paragraph(b, bullet=True) for b in slide.bullets
        ) or _pptx_paragraph("")
        body_shape = (
            "<p:sp><p:nvSpPr>"
            f'<p:cNvPr id="3" name="Content {index}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>'
            f"{bullets}</p:txBody></p:sp>"
        )
        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            "<p:grpSpPr/>"
            f"{title_para}{body_shape}"
            "</p:spTree></p:cSld><p:clrMapOvr><a:overrideClrMapping "
            'bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
            'hlink="hlink" folHlink="folHlink"/></p:clrMapOvr></p:sld>'
        )
        slide_parts[f"ppt/slides/slide{index}.xml"] = slide_xml
        slide_rels[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            "</Relationships>"
        )
        rid = f"rId{index + 1}"  # rId1 reserved for the slide master
        presentation_slide_ids.append(f'<p:sldId id="{255 + index}" r:id="{rid}"/>')
        presentation_rels.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{index}.xml"/>'
        )

    presentation = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f"<p:sldIdLst>{''.join(presentation_slide_ids)}</p:sldIdLst>"
        '<p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    )
    presentation_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        + "".join(presentation_rels)
        + "</Relationships>"
    )

    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": _pptx_content_types(len(deck)),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            "</Relationships>"
        ),
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": presentation_rels_xml,
        "ppt/slideMasters/slideMaster1.xml": _pptx_slide_master(),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
            "</Relationships>"
        ),
        "ppt/slideLayouts/slideLayout1.xml": _pptx_slide_layout(),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
            "</Relationships>"
        ),
        "ppt/theme/theme1.xml": _pptx_theme(),
    }
    parts.update(slide_parts)
    parts.update(slide_rels)
    return _zip_package(parts)


def _pptx_content_types(slide_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        f"{overrides}</Types>"
    )


def _pptx_slide_master() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/></p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
        'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        "</p:sldMaster>"
    )


def _pptx_slide_layout() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        "<p:grpSpPr/></p:spTree></p:cSld>"
        '<p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
        'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/></p:clrMapOvr>'
        "</p:sldLayout>"
    )


def _pptx_theme() -> str:
    scheme_colors = "".join(
        f'<a:{tag}><a:srgbClr val="{val}"/></a:{tag}>'
        for tag, val in (
            ("dk1", "000000"),
            ("lt1", "FFFFFF"),
            ("dk2", "44546A"),
            ("lt2", "E7E6E6"),
            ("accent1", "4472C4"),
            ("accent2", "ED7D31"),
            ("accent3", "A5A5A5"),
            ("accent4", "FFC000"),
            ("accent5", "5B9BD5"),
            ("accent6", "70AD47"),
            ("hlink", "0563C1"),
            ("folHlink", "954F72"),
        )
    )
    font_scheme = (
        '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/>'
        '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    )
    fmt_scheme = (
        '<a:fmtScheme name="Office">'
        '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
        '<a:lnStyleLst><a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme">'
        f'<a:themeElements><a:clrScheme name="Office">{scheme_colors}</a:clrScheme>'
        f"{font_scheme}{fmt_scheme}</a:themeElements></a:theme>"
    )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
# Line/character budget for the fixed-width Helvetica text PDF. A US-Letter page
# (612pt) with ~11pt Helvetica fits roughly 95 characters and ~52 lines; longer
# text is wrapped and paginated accordingly.
_PDF_LINES_PER_PAGE = 52
_PDF_MAX_CHARS_PER_LINE = 95


def _wrap(text: str, width: int) -> list[str]:
    """Wrap ``text`` to ``width`` characters, preserving explicit newlines."""
    out: list[str] = []
    for raw_line in text.split("\n"):
        if not raw_line:
            out.append("")
            continue
        words = raw_line.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                out.append(current)
                current = word
            else:
                current = candidate
        out.append(current)
    return out


def _pdf_escape(text: str) -> str:
    """Escape a string for a PDF text-showing operator."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(
    title: str | None,
    sections: list[Section],
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    """Build a minimal multi-page text PDF from the supplied content."""
    lines: list[tuple[str, bool]] = []  # (text, is_heading)
    if title:
        lines.append((title, True))
        lines.append(("", False))
    for section in sections:
        if section.heading:
            lines.append((section.heading, True))
        for wrapped in _wrap(section.body or "", _PDF_MAX_CHARS_PER_LINE):
            lines.append((wrapped, False))
        lines.append(("", False))
    if columns or rows:
        if columns:
            lines.append((" | ".join(str(c) for c in columns), True))
        for row in rows:
            text = " | ".join("" if c is None else str(c) for c in row)
            for wrapped in _wrap(text, _PDF_MAX_CHARS_PER_LINE):
                lines.append((wrapped, False))

    if not lines:
        raise ArtifactError("A PDF export needs a title, sections, or a table.")

    # Paginate the lines.
    pages: list[list[tuple[str, bool]]] = [
        lines[i : i + _PDF_LINES_PER_PAGE] for i in range(0, len(lines), _PDF_LINES_PER_PAGE)
    ]

    # Build PDF objects. Object 1: catalog, 2: pages, 3: font, then per page a
    # page object and a content stream object.
    objects: list[bytes] = []

    def add_object(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # 1-based object number

    # Reserve catalog(1) and pages(2) numbers by placeholders; fill after we know
    # the page object numbers.
    objects.append(b"")  # 1 catalog (placeholder)
    objects.append(b"")  # 2 pages (placeholder)
    font_num = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_nums: list[int] = []
    for page_lines in pages:
        content = _pdf_content_stream(page_lines)
        stream_num = add_object(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        page_body = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font_num).encode("ascii") + b" 0 R >> >> "
            b"/Contents " + str(stream_num).encode("ascii") + b" 0 R >>"
        )
        page_nums.append(add_object(page_body))

    kids = b" ".join(f"{n} 0 R".encode("ascii") for n in page_nums)
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(len(page_nums)).encode("ascii")
        + b" >>"
    )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    return _assemble_pdf(objects)


def _pdf_content_stream(page_lines: list[tuple[str, bool]]) -> bytes:
    """Return the content stream drawing ``page_lines`` top-to-bottom."""
    parts = [b"BT", b"/F1 11 Tf", b"14 TL", b"56 748 Td"]
    for text, is_heading in page_lines:
        size = b"13" if is_heading else b"11"
        parts.append(b"/F1 " + size + b" Tf")
        shown = _pdf_escape(text) if text else ""
        parts.append(b"(" + shown.encode("latin-1", errors="replace") + b") Tj")
        parts.append(b"T*")
    parts.append(b"ET")
    return b"\n".join(parts)


def _assemble_pdf(objects: list[bytes]) -> bytes:
    """Serialize numbered PDF objects with a cross-reference table and trailer."""
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_pos = len(out)
    count = len(objects) + 1
    out += f"xref\n0 {count}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        b"trailer\n<< /Size " + str(count).encode("ascii") + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode("ascii") + b"\n%%EOF"
    )
    return bytes(out)


# ---------------------------------------------------------------------------
# Per-request registry (outbound counterpart to current_attachments)
# ---------------------------------------------------------------------------
current_artifacts: contextvars.ContextVar[list[GeneratedFile]] = contextvars.ContextVar(
    "current_artifacts", default=[]
)


def set_current_artifacts() -> contextvars.Token:
    """Start a fresh artifact list for the active request; returns a reset token."""
    return current_artifacts.set([])


def add_current_artifact(file: GeneratedFile) -> None:
    """Register a generated file for the active request (appends to the list)."""
    current_artifacts.get().append(file)


def get_current_artifacts() -> list[GeneratedFile]:
    """Return the files generated during the active request."""
    return list(current_artifacts.get())
