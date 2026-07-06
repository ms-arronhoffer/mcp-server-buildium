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
  packages assembled with :mod:`zipfile`; PDF is a hand-written PDF whose table
  is laid out on a measured grid.
* **Provider-neutral.** The raw bytes never go to the model. A tool builds the
  file, registers it here, and the ``/chat`` route streams it to the browser as
  a base64 ``artifact`` event so the extension can offer a download link.
* **Presentation-ready.** A shared :data:`THEME_COLORS` palette drives every
  format so output looks professional, not sparse: PPTX decks are widescreen,
  themed, and can embed native charts (column/bar/line/pie) and tables; DOCX
  uses styled headings and a banded, colour-headed table; PDF renders a cover
  title, section copy, and a gridded table with a colour-filled header,
  zebra-striped rows and right-aligned numeric columns. A ``description``
  renders as a contextual lead paragraph (PDF), subtitle (DOCX) or cover
  subtitle (PPTX) so presentation formats are "board room ready" rather than a
  bare data dump.
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


# ---------------------------------------------------------------------------
# Shared brand theme
# ---------------------------------------------------------------------------
# A single, corporate-looking palette drives every format so decks, documents
# and PDFs share one identity. Colours are hex ``RRGGBB`` (no leading ``#``).
# ``accentN`` map onto the PPTX theme's ``accentN`` slots and are reused for
# chart series, table shading and heading colour.
THEME_COLORS: dict[str, str] = {
    "dk1": "1A2433",  # near-black body text
    "lt1": "FFFFFF",  # page/slide background
    "dk2": "44546A",  # secondary dark (subtitles)
    "lt2": "EEF2F7",  # light band / table zebra fill
    "accent1": "2F5597",  # primary deep blue (titles, header fill)
    "accent2": "4472C4",  # blue
    "accent3": "5B9BD5",  # light blue
    "accent4": "70AD47",  # green
    "accent5": "FFC000",  # amber
    "accent6": "C55A11",  # orange
    "hlink": "0563C1",
    "folHlink": "954F72",
}

# Ordered accent colours reused to colour successive chart series/segments.
_SERIES_COLORS: tuple[str, ...] = (
    THEME_COLORS["accent1"],
    THEME_COLORS["accent4"],
    THEME_COLORS["accent5"],
    THEME_COLORS["accent3"],
    THEME_COLORS["accent6"],
    THEME_COLORS["accent2"],
)


@dataclass
class Section:
    """A narrative section for document formats (DOCX, PDF)."""

    heading: str = ""
    body: str = ""


@dataclass
class Chart:
    """A simple chart to embed on a PPTX slide.

    ``kind`` is one of ``bar``/``column``, ``line`` or ``pie``. ``categories``
    are the x-axis labels; ``series`` is a list of ``(name, values)`` pairs with
    one numeric value per category. Pie charts use only the first series.
    """

    categories: list[str] = field(default_factory=list)
    series: list[tuple[str, list[float]]] = field(default_factory=list)
    kind: str = "column"
    title: str = ""


@dataclass
class Slide:
    """A single slide for the PPTX format.

    A slide may carry bullet text, an embedded :class:`Chart`, or both. Setting
    ``layout='title'`` renders a centred title slide (cover) instead of the
    standard title-and-content layout.
    """

    title: str = ""
    bullets: list[str] = field(default_factory=list)
    subtitle: str = ""
    chart: Chart | None = None
    layout: str = "content"


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
    description: str | None = None,
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
        description: Optional contextual summary rendered beneath the title as a
            lead paragraph (PDF), subtitle (DOCX) or cover subtitle (PPTX). This
            is what makes presentation formats "board room ready" — a short
            paragraph explaining what the artifact shows and why it matters.
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
    description = (description or "").strip() or None

    if fmt == "csv":
        data = _build_csv(columns, rows)
    elif fmt == "xlsx":
        data = _build_xlsx(title, columns, rows)
    elif fmt == "docx":
        data = _build_docx(title, description, sections, columns, rows)
    elif fmt == "pdf":
        data = _build_pdf(title, description, sections, columns, rows)
    else:  # pptx
        data = _build_pptx(title, description, slides, columns, rows)

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


def _docx_paragraph(
    text: str,
    *,
    bold: bool = False,
    size: int | None = None,
    color: str | None = None,
    style: str | None = None,
    spacing_before: int | None = None,
    spacing_after: int | None = None,
) -> str:
    """Return a WordprocessingML paragraph for ``text`` (blank when empty)."""
    p_props_parts: list[str] = []
    if style:
        p_props_parts.append(f'<w:pStyle w:val="{style}"/>')
    if spacing_before is not None or spacing_after is not None:
        before = f' w:before="{spacing_before}"' if spacing_before is not None else ""
        after = f' w:after="{spacing_after}"' if spacing_after is not None else ""
        p_props_parts.append(f"<w:spacing{before}{after}/>")
    p_props = f"<w:pPr>{''.join(p_props_parts)}</w:pPr>" if p_props_parts else ""

    run_props_parts: list[str] = []
    if bold:
        run_props_parts.append("<w:b/>")
    if color:
        run_props_parts.append(f'<w:color w:val="{color}"/>')
    if size:
        run_props_parts.append(f'<w:sz w:val="{size * 2}"/>')
    run_props = f"<w:rPr>{''.join(run_props_parts)}</w:rPr>" if run_props_parts else ""

    if not text:
        return f"<w:p>{p_props}</w:p>"
    return (
        f'<w:p>{p_props}<w:r>{run_props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def _docx_table(columns: list[str], rows: list[list[object]]) -> str:
    """Return a professional, banded WordprocessingML table for tabular content."""
    header_fill = THEME_COLORS["accent1"]
    zebra_fill = THEME_COLORS["lt2"]
    border_color = "D9D9D9"
    border = (
        "<w:tblBorders>"
        + "".join(
            f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="{border_color}"/>'
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + "</w:tblBorders>"
    )
    tbl_pr = (
        "<w:tblPr>"
        '<w:tblW w:w="5000" w:type="pct"/>'
        f"{border}"
        "<w:tblCellMar>"
        '<w:top w:w="60" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
        '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="108" w:type="dxa"/>'
        "</w:tblCellMar>"
        "</w:tblPr>"
    )

    def _cell(value: object, *, header: bool, fill: str | None) -> str:
        shade = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
        tc_pr = f'<w:tcPr><w:tcW w:w="0" w:type="auto"/>{shade}<w:vAlign w:val="center"/></w:tcPr>'
        run_parts = []
        if header:
            run_parts.append("<w:b/>")
            run_parts.append(f'<w:color w:val="{THEME_COLORS["lt1"]}"/>')
        run_props = f"<w:rPr>{''.join(run_parts)}</w:rPr>" if run_parts else ""
        text = "" if value is None else str(value)
        return (
            f"<w:tc>{tc_pr}"
            f'<w:p><w:pPr><w:spacing w:before="20" w:after="20"/></w:pPr>'
            f'<w:r>{run_props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:tc>'
        )

    table_rows: list[str] = []
    if columns:
        table_rows.append(
            "<w:tr>"
            + "".join(_cell(col, header=True, fill=header_fill) for col in columns)
            + "</w:tr>"
        )
    for r_index, row in enumerate(rows):
        fill = zebra_fill if r_index % 2 == 1 else None
        table_rows.append(
            "<w:tr>" + "".join(_cell(cell, header=False, fill=fill) for cell in row) + "</w:tr>"
        )
    return f"<w:tbl>{tbl_pr}{''.join(table_rows)}</w:tbl>"


def _docx_styles() -> str:
    """Return a ``styles.xml`` defining professional Title/Heading/Normal styles."""
    accent = THEME_COLORS["accent1"]
    dk2 = THEME_COLORS["dk2"]
    body = THEME_COLORS["dk1"]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        f'<w:color w:val="{body}"/><w:sz w:val="22"/>'
        "</w:rPr></w:rPrDefault>"
        '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="264" w:lineRule="auto"/></w:pPr>'
        "</w:pPrDefault></w:docDefaults>"
        # Normal
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'
        # Title
        '<w:style w:type="paragraph" w:styleId="Title">'
        '<w:name w:val="Title"/>'
        '<w:pPr><w:spacing w:before="0" w:after="80"/></w:pPr>'
        '<w:rPr><w:rFonts w:ascii="Calibri Light" w:hAnsi="Calibri Light"/>'
        f'<w:b/><w:color w:val="{accent}"/><w:sz w:val="56"/></w:rPr></w:style>'
        # Subtitle
        '<w:style w:type="paragraph" w:styleId="Subtitle">'
        '<w:name w:val="Subtitle"/>'
        '<w:pPr><w:spacing w:before="0" w:after="240"/></w:pPr>'
        f'<w:rPr><w:color w:val="{dk2}"/><w:sz w:val="26"/></w:rPr></w:style>'
        # Heading 1
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/>'
        '<w:pPr><w:spacing w:before="280" w:after="120"/>'
        f'<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="{accent}"/></w:pBdr></w:pPr>'
        '<w:rPr><w:rFonts w:ascii="Calibri Light" w:hAnsi="Calibri Light"/>'
        f'<w:b/><w:color w:val="{accent}"/><w:sz w:val="30"/></w:rPr></w:style>'
        "</w:styles>"
    )


def _build_docx(
    title: str | None,
    description: str | None,
    sections: list[Section],
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    """Build a styled, professional DOCX (WordprocessingML) document."""
    if not (title or description or sections or columns or rows):
        raise ArtifactError("A document export needs a title, sections, or a table.")

    body: list[str] = []
    if title:
        body.append(_docx_paragraph(title, style="Title"))
    if description:
        for line in description.split("\n"):
            body.append(_docx_paragraph(line, style="Subtitle"))
    for section in sections:
        if section.heading:
            body.append(_docx_paragraph(section.heading, style="Heading1"))
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
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    return _zip_package(
        {
            "[Content_Types].xml": content_types,
            "_rels/.rels": root_rels,
            "word/document.xml": document,
            "word/_rels/document.xml.rels": document_rels,
            "word/styles.xml": _docx_styles(),
        }
    )


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------
# Widescreen 16:9 canvas (13.333in x 7.5in) in English Metric Units (914400/in).
_PPTX_W = 12192000
_PPTX_H = 6858000
_PPTX_MARGIN = 640080  # ~0.7in left/right content margin
_PPTX_STRIPE = 137160  # ~0.15in brand stripe width
_PPTX_CONTENT_W = _PPTX_W - 2 * _PPTX_MARGIN

# Cap categories/series on an auto-derived chart so a large table stays legible.
_MAX_DERIVED_CHART_CATEGORIES = 12
_MAX_DERIVED_CHART_SERIES = 4


def _pptx_run(text: str, *, size: int, bold: bool, color: str) -> str:
    """Return a formatted DrawingML text run (``size`` in points)."""
    return (
        f'<a:r><a:rPr lang="en-US" sz="{size * 100}" b="{1 if bold else 0}" dirty="0">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr>'
        f"<a:t>{escape(text)}</a:t></a:r>"
    )


def _pptx_para(
    text: str,
    *,
    size: int = 18,
    bold: bool = False,
    color: str | None = None,
    align: str = "l",
    bullet: bool = False,
) -> str:
    """Return a formatted DrawingML paragraph."""
    color = color or THEME_COLORS["dk1"]
    if bullet:
        marker = (
            f'<a:buClr><a:srgbClr val="{THEME_COLORS["accent1"]}"/></a:buClr>'
            '<a:buFont typeface="Arial"/><a:buChar char="&#8226;"/>'
        )
        p_pr = f'<a:pPr marL="285750" indent="-285750" algn="{align}">{marker}</a:pPr>'
    else:
        p_pr = f'<a:pPr algn="{align}"><a:buNone/></a:pPr>'
    if not text:
        return f"<a:p>{p_pr}</a:p>"
    return f"<a:p>{p_pr}{_pptx_run(text, size=size, bold=bold, color=color)}</a:p>"


def _pptx_textbox(
    shape_id: int,
    name: str,
    *,
    x: int,
    y: int,
    cx: int,
    cy: int,
    paragraphs: str,
    anchor: str = "t",
) -> str:
    """Return a positioned text-box shape containing ``paragraphs``."""
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}"><a:normAutofit/></a:bodyPr>'
        f"<a:lstStyle/>{paragraphs or '<a:p/>'}</p:txBody></p:sp>"
    )


def _pptx_rect(shape_id: int, name: str, *, x: int, y: int, cx: int, cy: int, fill: str) -> str:
    """Return a positioned, solid-filled rectangle (decorative graphic)."""
    return (
        "<p:sp><p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
        "<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>'
        "<p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>"
    )


def _pptx_graphic_frame_chart(shape_id: int, rid: str, *, x: int, y: int, cx: int, cy: int) -> str:
    """Return a graphic frame that embeds the chart referenced by ``rid``."""
    return (
        "<p:graphicFrame><p:nvGraphicFramePr>"
        f'<p:cNvPr id="{shape_id}" name="Chart {shape_id}"/>'
        "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
        f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        f'r:id="{rid}"/></a:graphicData></a:graphic></p:graphicFrame>'
    )


def _pptx_table_frame(
    shape_id: int, columns: list[str], rows: list[list[object]], *, x: int, y: int, cx: int, cy: int
) -> str:
    """Return a graphic frame containing a styled, banded native PPTX table."""
    n_cols = max(len(columns), max((len(r) for r in rows), default=0)) or 1
    col_w = cx // n_cols
    grid = "".join(f'<a:gridCol w="{col_w}"/>' for _ in range(n_cols))

    def _tc(value: object, *, header: bool, fill: str | None) -> str:
        text = "" if value is None else str(value)
        color = THEME_COLORS["lt1"] if header else THEME_COLORS["dk1"]
        run = _pptx_run(text, size=13 if header else 12, bold=header, color=color)
        body = f'<a:p><a:pPr algn="l"/>{run if text else ""}</a:p>'
        fill_xml = (
            f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
        )
        return (
            f"<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{body}</a:txBody>"
            f'<a:tcPr marL="45720" marR="45720" marT="22860" marB="22860" anchor="ctr">'
            f"{fill_xml}</a:tcPr></a:tc>"
        )

    trs: list[str] = []
    if columns:
        cells = "".join(
            _tc(columns[i] if i < len(columns) else "", header=True, fill=THEME_COLORS["accent1"])
            for i in range(n_cols)
        )
        trs.append(f'<a:tr h="370840">{cells}</a:tr>')
    for r_index, row in enumerate(rows):
        fill = THEME_COLORS["lt2"] if r_index % 2 == 1 else THEME_COLORS["lt1"]
        cells = "".join(
            _tc(row[i] if i < len(row) else "", header=False, fill=fill) for i in range(n_cols)
        )
        trs.append(f'<a:tr h="320040">{cells}</a:tr>')

    return (
        "<p:graphicFrame><p:nvGraphicFramePr>"
        f'<p:cNvPr id="{shape_id}" name="Table {shape_id}"/>'
        "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
        f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></p:xfrm>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        '<a:tbl><a:tblPr firstRow="1" bandRow="1"/>'
        f"<a:tblGrid>{grid}</a:tblGrid>{''.join(trs)}</a:tbl>"
        "</a:graphicData></a:graphic></p:graphicFrame>"
    )


def _num(value: object) -> float | None:
    """Coerce ``value`` to a float, tolerating currency/percent formatting."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace("%", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _chart_from_table(columns: list[str], rows: list[list[object]]) -> Chart | None:
    """Derive a column chart from the first label column + numeric columns."""
    if not rows or len(columns) < 2:
        return None
    categories = [str(r[0]) if r else "" for r in rows]
    series: list[tuple[str, list[float]]] = []
    for c_index in range(1, len(columns)):
        values: list[float] = []
        numeric = True
        for row in rows:
            cell = row[c_index] if c_index < len(row) else None
            num = _num(cell)
            if num is None:
                numeric = False
                break
            values.append(num)
        if numeric and values:
            series.append((str(columns[c_index]), values))
    if not series:
        return None
    # Keep the deck readable: cap categories/series for a derived chart.
    if len(categories) > _MAX_DERIVED_CHART_CATEGORIES:
        categories = categories[:_MAX_DERIVED_CHART_CATEGORIES]
        series = [(name, vals[:_MAX_DERIVED_CHART_CATEGORIES]) for name, vals in series]
    return Chart(categories=categories, series=series[:_MAX_DERIVED_CHART_SERIES], kind="column")


def _num_cache(values: list[float]) -> str:
    pts = "".join(f'<c:pt idx="{i}"><c:v>{v:g}</c:v></c:pt>' for i, v in enumerate(values))
    return (
        f"<c:numCache><c:formatCode>General</c:formatCode>"
        f'<c:ptCount val="{len(values)}"/>{pts}</c:numCache>'
    )


def _str_cache(labels: list[str]) -> str:
    pts = "".join(f'<c:pt idx="{i}"><c:v>{escape(v)}</c:v></c:pt>' for i, v in enumerate(labels))
    return f'<c:strCache><c:ptCount val="{len(labels)}"/>{pts}</c:strCache>'


def _pptx_chart_xml(chart: Chart) -> str:
    """Return a standalone chart part rendering ``chart`` from cached values."""
    kind = (chart.kind or "column").strip().lower()
    categories = [str(c) for c in chart.categories]
    series = [(str(n), [float(v) for v in vals]) for n, vals in chart.series if vals]
    if not series:
        raise ArtifactError("A chart needs at least one data series.")
    n_cat = len(categories)

    title_xml = ""
    if chart.title:
        title_xml = (
            "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
            f'<a:p><a:pPr><a:defRPr sz="1400" b="1">'
            f'<a:solidFill><a:srgbClr val="{THEME_COLORS["dk2"]}"/></a:solidFill>'
            "</a:defRPr></a:pPr>"
            f'<a:r><a:rPr lang="en-US"/><a:t>{escape(chart.title)}</a:t></a:r></a:p>'
            '</c:rich></c:tx><c:overlay val="0"/></c:title>'
        )

    def _cat_ref() -> str:
        return (
            f"<c:cat><c:strRef><c:f>Sheet1!$A$2:$A${n_cat + 1}</c:f>"
            f"{_str_cache(categories)}</c:strRef></c:cat>"
        )

    if kind in ("pie", "doughnut"):
        name, values = series[0]
        d_pts = "".join(
            f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/>'
            f'<c:spPr><a:solidFill><a:srgbClr val="{_SERIES_COLORS[i % len(_SERIES_COLORS)]}"/>'
            "</a:solidFill></c:spPr></c:dPt>"
            for i in range(len(values))
        )
        plot = (
            '<c:pieChart><c:varyColors val="1"/>'
            '<c:ser><c:idx val="0"/><c:order val="0"/>'
            f"<c:tx><c:strRef><c:f>Sheet1!$B$1</c:f>{_str_cache([name])}</c:strRef></c:tx>"
            f"{d_pts}{_cat_ref()}"
            f"<c:val><c:numRef><c:f>Sheet1!$B$2:$B${n_cat + 1}</c:f>{_num_cache(values)}"
            "</c:numRef></c:val></c:ser>"
            '<c:firstSliceAng val="0"/></c:pieChart>'
        )
        axes = ""
    else:
        is_line = kind == "line"
        sers: list[str] = []
        for s_index, (name, values) in enumerate(series):
            color = _SERIES_COLORS[s_index % len(_SERIES_COLORS)]
            col_letter = chr(ord("B") + s_index)
            if is_line:
                sp_pr = f'<c:spPr><a:ln w="28575"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr>'
                marker = '<c:marker><c:symbol val="circle"/><c:size val="6"/></c:marker>'
            else:
                sp_pr = f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></c:spPr>'
                marker = ""
            sers.append(
                f'<c:ser><c:idx val="{s_index}"/><c:order val="{s_index}"/>'
                f"<c:tx><c:strRef><c:f>Sheet1!${col_letter}$1</c:f>{_str_cache([name])}"
                "</c:strRef></c:tx>"
                f"{sp_pr}{marker}{_cat_ref()}"
                f"<c:val><c:numRef><c:f>Sheet1!${col_letter}$2:${col_letter}${n_cat + 1}</c:f>"
                f"{_num_cache(values)}</c:numRef></c:val></c:ser>"
            )
        if is_line:
            plot = (
                '<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
                + "".join(sers)
                + '<c:marker val="1"/><c:axId val="111111111"/><c:axId val="222222222"/></c:lineChart>'
            )
        else:
            plot = (
                '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
                '<c:varyColors val="0"/>'
                + "".join(sers)
                + '<c:gapWidth val="120"/><c:axId val="111111111"/><c:axId val="222222222"/></c:barChart>'
            )
        axes = (
            '<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            '<c:delete val="0"/><c:axPos val="b"/>'
            f'<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1000">'
            f'<a:solidFill><a:srgbClr val="{THEME_COLORS["dk2"]}"/></a:solidFill></a:defRPr></a:pPr>'
            '<a:endParaRPr lang="en-US"/></a:p></c:txPr>'
            '<c:crossAx val="222222222"/></c:catAx>'
            '<c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            '<c:delete val="0"/><c:axPos val="l"/>'
            '<c:majorGridlines><c:spPr><a:ln><a:solidFill><a:srgbClr val="E2E7EE"/></a:solidFill>'
            "</a:ln></c:spPr></c:majorGridlines>"
            f'<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1000">'
            f'<a:solidFill><a:srgbClr val="{THEME_COLORS["dk2"]}"/></a:solidFill></a:defRPr></a:pPr>'
            '<a:endParaRPr lang="en-US"/></a:p></c:txPr>'
            '<c:crossAx val="111111111"/></c:valAx>'
        )

    legend = '<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<c:chart>"
        f"{title_xml}"
        f'<c:autoTitleDeleted val="{0 if chart.title else 1}"/>'
        f"<c:plotArea><c:layout/>{plot}{axes}</c:plotArea>"
        f"{legend}"
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        "</c:chart></c:chartSpace>"
    )


def _pptx_slide_shapes(slide: Slide, base_id: int) -> tuple[str, list[str]]:
    """Return (shape XML, chart parts) for one slide.

    ``chart parts`` is a list of chart XML strings this slide references (in
    relationship order rId2, rId3, ...).
    """
    stripe = _pptx_rect(
        base_id, "Brand Stripe", x=0, y=0, cx=_PPTX_STRIPE, cy=_PPTX_H, fill=THEME_COLORS["accent1"]
    )
    charts: list[str] = []

    if slide.layout == "title":
        # Cover slide: full accent background with centred title/subtitle.
        bg = _pptx_rect(
            base_id, "Cover", x=0, y=0, cx=_PPTX_W, cy=_PPTX_H, fill=THEME_COLORS["accent1"]
        )
        accent_bar = _pptx_rect(
            base_id + 1,
            "Accent",
            x=_PPTX_MARGIN,
            y=3200400,
            cx=1828800,
            cy=68580,
            fill=THEME_COLORS["accent5"],
        )
        title_para = _pptx_para(
            slide.title or "Presentation", size=44, bold=True, color=THEME_COLORS["lt1"], align="l"
        )
        title_box = _pptx_textbox(
            base_id + 2,
            "Title",
            x=_PPTX_MARGIN,
            y=2300000,
            cx=_PPTX_CONTENT_W,
            cy=900000,
            paragraphs=title_para,
        )
        shapes = bg + accent_bar + title_box
        if slide.subtitle:
            sub_para = _pptx_para(
                slide.subtitle, size=22, bold=False, color=THEME_COLORS["lt2"], align="l"
            )
            shapes += _pptx_textbox(
                base_id + 3,
                "Subtitle",
                x=_PPTX_MARGIN,
                y=3350000,
                cx=_PPTX_CONTENT_W,
                cy=700000,
                paragraphs=sub_para,
            )
        return shapes, charts

    # Standard content slide.
    title_text = slide.title or "Slide"
    title_para = _pptx_para(title_text, size=30, bold=True, color=THEME_COLORS["accent1"])
    title_box = _pptx_textbox(
        base_id,
        "Title",
        x=_PPTX_MARGIN,
        y=320040,
        cx=_PPTX_CONTENT_W,
        cy=850000,
        paragraphs=title_para,
    )
    rule = _pptx_rect(
        base_id + 1,
        "Rule",
        x=_PPTX_MARGIN,
        y=1150000,
        cx=_PPTX_CONTENT_W,
        cy=27432,
        fill=THEME_COLORS["accent3"],
    )
    shapes = stripe + title_box + rule

    content_top = 1360000
    content_h = _PPTX_H - content_top - 400000
    has_bullets = bool(slide.bullets)
    has_chart = slide.chart is not None

    next_id = base_id + 2
    if slide.subtitle:
        sub_para = _pptx_para(slide.subtitle, size=16, bold=False, color=THEME_COLORS["dk2"])
        shapes += _pptx_textbox(
            next_id,
            "Subtitle",
            x=_PPTX_MARGIN,
            y=1180000,
            cx=_PPTX_CONTENT_W,
            cy=300000,
            paragraphs=sub_para,
        )
        next_id += 1

    if has_bullets and has_chart:
        half = (_PPTX_CONTENT_W - 274320) // 2
        bullets = "".join(_pptx_para(b, size=16, bullet=True) for b in slide.bullets) or "<a:p/>"
        shapes += _pptx_textbox(
            next_id,
            "Content",
            x=_PPTX_MARGIN,
            y=content_top,
            cx=half,
            cy=content_h,
            paragraphs=bullets,
        )
        next_id += 1
        charts.append(_pptx_chart_xml(slide.chart))
        shapes += _pptx_graphic_frame_chart(
            next_id, "rId2", x=_PPTX_MARGIN + half + 274320, y=content_top, cx=half, cy=content_h
        )
        next_id += 1
    elif has_chart:
        charts.append(_pptx_chart_xml(slide.chart))
        shapes += _pptx_graphic_frame_chart(
            next_id, "rId2", x=_PPTX_MARGIN, y=content_top, cx=_PPTX_CONTENT_W, cy=content_h
        )
        next_id += 1
    else:
        bullets = "".join(_pptx_para(b, size=18, bullet=True) for b in slide.bullets) or "<a:p/>"
        shapes += _pptx_textbox(
            next_id,
            "Content",
            x=_PPTX_MARGIN,
            y=content_top,
            cx=_PPTX_CONTENT_W,
            cy=content_h,
            paragraphs=bullets,
        )
        next_id += 1

    return shapes, charts


def _build_pptx(
    title: str | None,
    description: str | None,
    slides: list[Slide],
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    """Build a themed, presentation-ready PPTX (PresentationML) slide deck."""
    deck: list[Slide] = list(slides)
    table_slide_index: int | None = None
    if not deck:
        # Derive slides from a title and/or a table when explicit slides are absent.
        if title or description:
            deck.append(
                Slide(title=title or "Presentation", subtitle=description or "", layout="title")
            )
        if columns or rows:
            table_slide_index = len(deck)
            deck.append(Slide(title="Data", bullets=[]))
    elif description:
        # Explicit slides were supplied: give them board-room context by leading
        # with a cover slide that carries the deck's description, unless the deck
        # already opens with a cover we can annotate.
        first = deck[0]
        if first.layout == "title" and not first.subtitle:
            first.subtitle = description
        else:
            deck.insert(
                0,
                Slide(
                    title=title or first.title or "Presentation",
                    subtitle=description,
                    layout="title",
                ),
            )
    if not deck:
        raise ArtifactError("A slide deck export needs 'slides', a title, or a table.")

    slide_parts: dict[str, str] = {}
    slide_rels: dict[str, str] = {}
    chart_parts: dict[str, str] = {}
    chart_count = 0
    presentation_slide_ids: list[str] = []
    presentation_rels: list[str] = []

    for index, slide in enumerate(deck, start=1):
        shapes, charts = _pptx_slide_shapes(slide, base_id=10)

        extra_frames = ""
        # The derived table slide renders an actual native table (not text).
        if table_slide_index is not None and index == table_slide_index + 1 and (columns or rows):
            extra_frames = _pptx_table_frame(
                90,
                columns,
                rows[:14],
                x=_PPTX_MARGIN,
                y=1360000,
                cx=_PPTX_CONTENT_W,
                cy=_PPTX_H - 1360000 - 400000,
            )
            derived = _chart_from_table(columns, rows)
            if derived is not None:
                # Split: table left, chart right.
                half = (_PPTX_CONTENT_W - 274320) // 2
                extra_frames = _pptx_table_frame(
                    90,
                    columns,
                    rows[:14],
                    x=_PPTX_MARGIN,
                    y=1360000,
                    cx=half,
                    cy=_PPTX_H - 1360000 - 400000,
                )
                charts = [_pptx_chart_xml(derived)]
                shapes += _pptx_graphic_frame_chart(
                    91,
                    "rId2",
                    x=_PPTX_MARGIN + half + 274320,
                    y=1360000,
                    cx=half,
                    cy=_PPTX_H - 1360000 - 400000,
                )
        shapes += extra_frames

        # Build slide relationships: rId1 is always the layout; charts follow.
        rels = [
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/>'
        ]
        for c_offset, chart_xml in enumerate(charts):
            chart_count += 1
            chart_name = f"ppt/charts/chart{chart_count}.xml"
            chart_parts[chart_name] = chart_xml
            rid = f"rId{c_offset + 2}"
            rels.append(
                f'<Relationship Id="{rid}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
                f'Target="../charts/chart{chart_count}.xml"/>'
            )

        slide_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            "<p:cSld><p:spTree>"
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            "<p:grpSpPr/>"
            f"{shapes}"
            "</p:spTree></p:cSld><p:clrMapOvr><a:overrideClrMapping "
            'bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
            'hlink="hlink" folHlink="folHlink"/></p:clrMapOvr></p:sld>'
        )
        slide_parts[f"ppt/slides/slide{index}.xml"] = slide_xml
        slide_rels[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels)
            + "</Relationships>"
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
        f'<p:sldSz cx="{_PPTX_W}" cy="{_PPTX_H}"/><p:notesSz cx="6858000" cy="9144000"/>'
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
        "[Content_Types].xml": _pptx_content_types(len(deck), chart_count),
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
    parts.update(chart_parts)
    return _zip_package(parts)


def _pptx_content_types(slide_count: int, chart_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    chart_overrides = "".join(
        f'<Override PartName="/ppt/charts/chart{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        for i in range(1, chart_count + 1)
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
        f"{overrides}{chart_overrides}</Types>"
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
        f'<a:{tag}><a:srgbClr val="{THEME_COLORS[key]}"/></a:{tag}>'
        for tag, key in (
            ("dk1", "dk1"),
            ("lt1", "lt1"),
            ("dk2", "dk2"),
            ("lt2", "lt2"),
            ("accent1", "accent1"),
            ("accent2", "accent2"),
            ("accent3", "accent3"),
            ("accent4", "accent4"),
            ("accent5", "accent5"),
            ("accent6", "accent6"),
            ("hlink", "hlink"),
            ("folHlink", "folHlink"),
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
# Executive-grade PDF layout. Unlike the earlier fixed-width "typewriter" export
# (which pipe-joined table cells into a single Helvetica line so columns never
# actually lined up), this renderer measures text with the Core-14 Helvetica
# metrics and lays content out on a real grid: a titled cover band, styled
# section headings, wrapped body copy, and a genuine table with proportional
# columns, a colour-filled header, zebra-striped rows, right-aligned numeric
# cells and repeating headers across page breaks. Everything is still produced
# with the standard library only (no reportlab/fpdf dependency).

# Page geometry (US-Letter, 612x792 points) and content margins.
_PDF_PAGE_W = 612.0
_PDF_PAGE_H = 792.0
_PDF_MARGIN_L = 54.0
_PDF_MARGIN_R = 54.0
_PDF_MARGIN_TOP = 54.0
_PDF_MARGIN_BOTTOM = 56.0
_PDF_CONTENT_W = _PDF_PAGE_W - _PDF_MARGIN_L - _PDF_MARGIN_R
_PDF_CONTENT_TOP = _PDF_PAGE_H - _PDF_MARGIN_TOP
# Hairline colour for table row separators (light blue-grey).
_PDF_BORDER = "C9D2DE"

# Adobe Core-14 advance widths (units of 1/1000 em) for printable ASCII
# (codepoints 32..126). These let us measure real string widths so wrapping and
# column sizing match what a viewer renders, instead of guessing a fixed
# characters-per-line budget.
_HELV_W = (
    278,
    278,
    355,
    556,
    556,
    889,
    667,
    191,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    278,
    278,
    584,
    584,
    584,
    556,
    1015,
    667,
    667,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    500,
    667,
    556,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    278,
    278,
    278,
    469,
    556,
    333,
    556,
    556,
    500,
    556,
    556,
    278,
    556,
    556,
    222,
    222,
    500,
    222,
    833,
    556,
    556,
    556,
    556,
    333,
    500,
    278,
    556,
    500,
    722,
    500,
    500,
    500,
    334,
    260,
    334,
    584,
)
_HELV_BOLD_W = (
    278,
    333,
    474,
    556,
    556,
    889,
    722,
    238,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    333,
    333,
    584,
    584,
    584,
    611,
    975,
    722,
    722,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    556,
    722,
    611,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    333,
    278,
    333,
    584,
    556,
    333,
    556,
    611,
    556,
    611,
    556,
    333,
    611,
    611,
    278,
    278,
    556,
    278,
    889,
    611,
    611,
    611,
    611,
    389,
    556,
    333,
    611,
    556,
    778,
    556,
    556,
    500,
    389,
    280,
    389,
    584,
)


def _char_width(ch: str, *, bold: bool) -> int:
    """Return the advance width (1/1000 em) of ``ch`` in Helvetica[-Bold]."""
    cp = ord(ch)
    if 32 <= cp <= 126:
        return (_HELV_BOLD_W if bold else _HELV_W)[cp - 32]
    return 600  # ~average Helvetica advance for non-ASCII glyphs (close enough for layout)


def _text_width(text: str, size: float, *, bold: bool = False) -> float:
    """Return the rendered width of ``text`` at ``size`` points."""
    return sum(_char_width(ch, bold=bold) for ch in text) * size / 1000.0


def _break_token(word: str, max_w: float, size: float, *, bold: bool) -> list[str]:
    """Hard-break a single over-long token (e.g. a long id) to fit ``max_w``."""
    out: list[str] = []
    current = ""
    for ch in word:
        candidate = current + ch
        if current and _text_width(candidate, size, bold=bold) > max_w:
            out.append(current)
            current = ch
        else:
            current = candidate
    if current:
        out.append(current)
    return out or [""]


def _wrap_width(text: str, max_w: float, size: float, *, bold: bool = False) -> list[str]:
    """Word-wrap ``text`` to ``max_w`` points, honouring explicit newlines."""
    lines: list[str] = []
    for raw_line in str(text).split("\n"):
        current = ""
        for word in raw_line.split(" "):
            if current and _text_width(f"{current} {word}", size, bold=bold) > max_w:
                lines.append(current)
                current = ""
            if not current and _text_width(word, size, bold=bold) > max_w:
                pieces = _break_token(word, max_w, size, bold=bold)
                lines.extend(pieces[:-1])
                current = pieces[-1]
            else:
                current = word if not current else f"{current} {word}"
        lines.append(current)
    return lines or [""]


def _pdf_escape(text: str) -> str:
    """Escape a string for a PDF text-showing operator."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _hex_to_pdf_rgb(hex_color: str) -> str:
    """Convert an ``RRGGBB`` hex colour to a PDF ``r g b`` operand string (0..1)."""
    r = int(hex_color[0:2], 16) / 255
    g = int(hex_color[2:4], 16) / 255
    b = int(hex_color[4:6], 16) / 255
    return f"{r:.3f} {g:.3f} {b:.3f}"


# Transliterate common typographic characters that have no Latin-1 glyph so the
# text renders cleanly (as ``-``/``"``/``...``) instead of a ``?`` placeholder.
_PDF_TRANSLATE = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2022": "-",
        "\u00b7": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "\u2009": " ",
        "\u202f": " ",
    }
)


def _op_text(x: float, y: float, text: str, font: bytes, size: float, color_hex: str) -> bytes:
    """Return a content-stream operator drawing ``text`` at (x, y)."""
    shown = _pdf_escape(text.translate(_PDF_TRANSLATE)).encode("latin-1", errors="replace")
    return (
        b"BT /"
        + font
        + f" {size:g} Tf ".encode("ascii")
        + _hex_to_pdf_rgb(color_hex).encode("ascii")
        + f" rg {x:.2f} {y:.2f} Td (".encode("ascii")
        + shown
        + b") Tj ET"
    )


def _op_rect(x: float, y: float, w: float, h: float, fill_hex: str) -> bytes:
    """Return a filled-rectangle operator (used for header/zebra bands)."""
    return f"{_hex_to_pdf_rgb(fill_hex)} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f".encode("ascii")


def _op_line(x1: float, y1: float, x2: float, y2: float, color_hex: str, width: float) -> bytes:
    """Return a stroked-line operator (rules and separators)."""
    return (
        f"{_hex_to_pdf_rgb(color_hex)} RG {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"
    ).encode("ascii")


class _PdfCanvas:
    """A minimal flowing-layout canvas that paginates as content is added.

    Content is appended top-to-bottom; ``_ensure`` starts a new page when the
    next block would cross the bottom margin. Each page accumulates a list of
    content-stream operators that :meth:`to_pdf_bytes` serialises into a valid
    PDF (with the same object/xref machinery as before).
    """

    def __init__(self) -> None:
        self.pages: list[list[bytes]] = [[]]
        self.y = _PDF_CONTENT_TOP
        self.has_content = False

    def _ops(self) -> list[bytes]:
        return self.pages[-1]

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = _PDF_CONTENT_TOP

    def _ensure(self, height: float) -> None:
        if self.y - height < _PDF_MARGIN_BOTTOM:
            self._new_page()

    # -- block builders -----------------------------------------------------
    def draw_title(self, title: str) -> None:
        self.has_content = True
        size = 22.0
        for line in _wrap_width(title, _PDF_CONTENT_W, size, bold=True):
            self._ensure(size + 8)
            baseline = self.y - size
            self._ops().append(
                _op_text(_PDF_MARGIN_L, baseline, line, b"F2", size, THEME_COLORS["accent1"])
            )
            self.y = baseline - 6
        self._ensure(12)
        rule_y = self.y
        self._ops().append(
            _op_line(
                _PDF_MARGIN_L,
                rule_y,
                _PDF_MARGIN_L + _PDF_CONTENT_W,
                rule_y,
                THEME_COLORS["accent3"],
                1.5,
            )
        )
        self.y = rule_y - 18

    def draw_description(self, text: str) -> None:
        """Draw a contextual lead paragraph in the secondary colour under the title."""
        self.has_content = True
        size = 11.5
        leading = 16.0
        for line in _wrap_width(text, _PDF_CONTENT_W, size, bold=False):
            self._ensure(leading)
            baseline = self.y - size
            if line:
                self._ops().append(
                    _op_text(_PDF_MARGIN_L, baseline, line, b"F1", size, THEME_COLORS["dk2"])
                )
            self.y -= leading
        self.y -= 8

    def draw_section(self, section: Section) -> None:
        if section.heading:
            self.has_content = True
            size = 13.0
            for line in _wrap_width(section.heading, _PDF_CONTENT_W, size, bold=True):
                self._ensure(size + 6)
                baseline = self.y - size
                self._ops().append(
                    _op_text(_PDF_MARGIN_L, baseline, line, b"F2", size, THEME_COLORS["accent1"])
                )
                self.y = baseline - 4
            self.y -= 2
        if section.body:
            self.has_content = True
            size = 10.5
            leading = 15.0
            for line in _wrap_width(section.body, _PDF_CONTENT_W, size, bold=False):
                self._ensure(leading)
                baseline = self.y - size
                if line:
                    self._ops().append(
                        _op_text(_PDF_MARGIN_L, baseline, line, b"F1", size, THEME_COLORS["dk1"])
                    )
                self.y -= leading
        self.y -= 10

    def draw_table(self, columns: list[str], rows: list[list[object]]) -> None:
        cols = [str(c) for c in columns]
        body = [["" if cell is None else str(cell) for cell in row] for row in rows]
        ncol = max([len(cols)] + [len(r) for r in body])
        if ncol == 0:
            return
        self.has_content = True
        cols += [""] * (ncol - len(cols))
        body = [r + [""] * (ncol - len(r)) for r in body]

        header_size = 10.0
        body_size = 10.0
        hpad = 7.0
        vpad = 6.0
        line_gap = 13.0

        # Proportional column widths from natural content width, scaled to fill
        # the printable width so the table reads as a full-width grid.
        natural: list[float] = []
        for ci in range(ncol):
            w = _text_width(cols[ci], header_size, bold=True)
            for r in body:
                w = max(w, _text_width(r[ci], body_size, bold=False))
            natural.append(w + 2 * hpad)
        scale = _PDF_CONTENT_W / (sum(natural) or 1.0)
        widths = [w * scale for w in natural]
        xs = [_PDF_MARGIN_L]
        for w in widths[:-1]:
            xs.append(xs[-1] + w)

        # Right-align columns whose non-empty cells are all numeric.
        right = []
        for ci in range(ncol):
            vals = [r[ci] for r in body if r[ci].strip()]
            right.append(bool(vals) and all(_num(v) is not None for v in vals))

        def cell_x(ci: int, text: str, size: float, *, bold: bool) -> float:
            if right[ci]:
                return xs[ci] + widths[ci] - hpad - _text_width(text, size, bold=bold)
            return xs[ci] + hpad

        def draw_header_row() -> None:
            cell_lines = [
                _wrap_width(cols[ci], widths[ci] - 2 * hpad, header_size, bold=True)
                for ci in range(ncol)
            ]
            nlines = max(len(cl) for cl in cell_lines)
            row_h = nlines * line_gap + 2 * vpad
            self._ensure(row_h)
            top = self.y
            self._ops().append(
                _op_rect(_PDF_MARGIN_L, top - row_h, _PDF_CONTENT_W, row_h, THEME_COLORS["accent1"])
            )
            for ci in range(ncol):
                for li, line in enumerate(cell_lines[ci]):
                    baseline = top - vpad - header_size - li * line_gap
                    self._ops().append(
                        _op_text(
                            cell_x(ci, line, header_size, bold=True),
                            baseline,
                            line,
                            b"F2",
                            header_size,
                            THEME_COLORS["lt1"],
                        )
                    )
            self.y = top - row_h

        draw_header_row()
        for ri, r in enumerate(body):
            cell_lines = [
                _wrap_width(r[ci], widths[ci] - 2 * hpad, body_size, bold=False)
                for ci in range(ncol)
            ]
            nlines = max((len(cl) for cl in cell_lines), default=1)
            row_h = nlines * line_gap + 2 * vpad
            if self.y - row_h < _PDF_MARGIN_BOTTOM:
                self._new_page()
                draw_header_row()
            top = self.y
            if ri % 2 == 1:
                self._ops().append(
                    _op_rect(_PDF_MARGIN_L, top - row_h, _PDF_CONTENT_W, row_h, THEME_COLORS["lt2"])
                )
            for ci in range(ncol):
                for li, line in enumerate(cell_lines[ci]):
                    if not line:
                        continue
                    baseline = top - vpad - body_size - li * line_gap
                    self._ops().append(
                        _op_text(
                            cell_x(ci, line, body_size, bold=False),
                            baseline,
                            line,
                            b"F1",
                            body_size,
                            THEME_COLORS["dk1"],
                        )
                    )
            bottom = top - row_h
            self._ops().append(
                _op_line(
                    _PDF_MARGIN_L, bottom, _PDF_MARGIN_L + _PDF_CONTENT_W, bottom, _PDF_BORDER, 0.5
                )
            )
            self.y = bottom
        self.y -= 12

    # -- serialisation ------------------------------------------------------
    def _add_footers(self) -> None:
        """Draw a centred ``Page N of M`` footer on every page."""
        total = len(self.pages)
        size = 8.0
        for idx, ops in enumerate(self.pages, start=1):
            label = f"Page {idx} of {total}"
            x = _PDF_MARGIN_L + (_PDF_CONTENT_W - _text_width(label, size, bold=False)) / 2
            ops.append(
                _op_text(x, _PDF_MARGIN_BOTTOM - 24, label, b"F1", size, THEME_COLORS["dk2"])
            )

    def to_pdf_bytes(self) -> bytes:
        self._add_footers()
        objects: list[bytes] = []

        def add_object(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        objects.append(b"")  # 1 catalog (placeholder)
        objects.append(b"")  # 2 pages (placeholder)
        font_num = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        font_bold_num = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

        page_nums: list[int] = []
        for ops in self.pages:
            content = b"\n".join(ops)
            stream_num = add_object(
                b"<< /Length "
                + str(len(content)).encode("ascii")
                + b" >>\nstream\n"
                + content
                + b"\nendstream"
            )
            page_body = (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 " + str(font_num).encode("ascii") + b" 0 R "
                b"/F2 " + str(font_bold_num).encode("ascii") + b" 0 R >> >> "
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


def _build_pdf(
    title: str | None,
    description: str | None,
    sections: list[Section],
    columns: list[str],
    rows: list[list[object]],
) -> bytes:
    """Build a styled, multi-page PDF (cover title, sections, real table)."""
    if not (title or description or sections or columns or rows):
        raise ArtifactError("A PDF export needs a title, sections, or a table.")

    canvas = _PdfCanvas()
    if title:
        canvas.draw_title(title)
    if description:
        canvas.draw_description(description)
    for section in sections:
        canvas.draw_section(section)
    if columns or rows:
        canvas.draw_table(columns, rows)

    if not canvas.has_content:
        raise ArtifactError("A PDF export needs a title, sections, or a table.")
    return canvas.to_pdf_bytes()


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
