"""Tests for assistant-generated downloadable files (llm.artifacts)."""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from mcp_server_buildium.llm.artifacts import (
    MAX_ARTIFACT_BYTES,
    ArtifactError,
    Chart,
    Section,
    Slide,
    add_current_artifact,
    build_generated_file,
    current_artifacts,
    get_current_artifacts,
    set_current_artifacts,
)

COLUMNS = ["Property", "Units", "Occupancy"]
ROWS = [["Maple Court", 12, "92%"], ["Oak Ridge", 8, "100%"]]
SECTIONS = [Section("Overview", "Your top properties.\nRanked by occupancy.")]
SLIDES = [Slide("Top Properties", ["Maple Court — 92%", "Oak Ridge — 100%"])]


def _ooxml_text(data: bytes) -> str:
    """Return the concatenated XML of an OOXML package, asserting validity."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert zf.testzip() is None
    blob = ""
    for name in zf.namelist():
        if name.endswith(".xml") or name.endswith(".rels"):
            ET.fromstring(zf.read(name))  # must be well-formed
            blob += zf.read(name).decode("utf-8", "replace")
    return blob


def test_csv_export_has_bom_and_rows() -> None:
    gf = build_generated_file(file_format="csv", filename="leases", columns=COLUMNS, rows=ROWS)
    assert gf.name == "leases.csv"
    assert gf.media_type == "text/csv"
    assert gf.data.startswith(b"\xef\xbb\xbf")  # Excel-friendly BOM
    text = gf.data.decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == COLUMNS
    assert parsed[1] == ["Maple Court", "12", "92%"]


def test_xlsx_export_is_valid_package_with_data() -> None:
    gf = build_generated_file(file_format="xlsx", title="Leases", columns=COLUMNS, rows=ROWS)
    assert gf.name.endswith(".xlsx")
    blob = _ooxml_text(gf.data)
    assert "Maple Court" in blob
    assert "Property" in blob
    # Numeric cells are stored as numbers, not inline strings.
    assert "<v>12</v>" in blob


def test_docx_export_contains_title_sections_and_table() -> None:
    gf = build_generated_file(
        file_format="docx",
        title="Property Report",
        sections=SECTIONS,
        columns=COLUMNS,
        rows=ROWS,
    )
    assert gf.name.endswith(".docx")
    blob = _ooxml_text(gf.data)
    assert "Property Report" in blob
    assert "Overview" in blob
    assert "Ranked by occupancy." in blob
    assert "<w:tbl>" in blob
    assert "Maple Court" in blob


def test_pdf_export_is_wellformed_and_paginates() -> None:
    big_rows = [[f"Property {i}", i, f"{i}%"] for i in range(200)]
    gf = build_generated_file(file_format="pdf", title="Big Report", columns=COLUMNS, rows=big_rows)
    assert gf.name.endswith(".pdf")
    assert gf.data.startswith(b"%PDF-1.4")
    assert gf.data.rstrip().endswith(b"%%EOF")
    # More than one page object for 200+ lines.
    assert gf.data.count(b"/Type /Page ") >= 2


def test_pptx_export_is_valid_package_with_slides() -> None:
    gf = build_generated_file(
        file_format="pptx",
        title="Deck",
        slides=[
            Slide("Top Properties", ["Maple Court", "Oak Ridge"]),
            Slide("Next Steps", ["Review vacancies"]),
        ],
    )
    assert gf.name.endswith(".pptx")
    zf = zipfile.ZipFile(io.BytesIO(gf.data))
    names = zf.namelist()
    assert "ppt/presentation.xml" in names
    assert "ppt/slides/slide1.xml" in names
    assert "ppt/slides/slide2.xml" in names
    assert "ppt/slideMasters/slideMaster1.xml" in names
    blob = _ooxml_text(gf.data)
    assert "Top Properties" in blob
    assert "Review vacancies" in blob


def test_pptx_derives_slide_from_table_when_no_slides() -> None:
    gf = build_generated_file(file_format="pptx", title="From Table", columns=COLUMNS, rows=ROWS)
    blob = _ooxml_text(gf.data)
    assert "Maple Court" in blob


def test_pptx_is_widescreen_and_uses_theme_accent() -> None:
    gf = build_generated_file(file_format="pptx", title="Deck", slides=SLIDES)
    zf = zipfile.ZipFile(io.BytesIO(gf.data))
    presentation = zf.read("ppt/presentation.xml").decode()
    # 16:9 widescreen canvas (12192000 x 6858000 EMU).
    assert 'cx="12192000"' in presentation
    theme = zf.read("ppt/theme/theme1.xml").decode()
    assert "2F5597" in theme  # brand accent1 propagated to the theme


def test_pptx_embeds_chart_part() -> None:
    gf = build_generated_file(
        file_format="pptx",
        slides=[
            Slide(
                title="Occupancy",
                bullets=["Maple leads"],
                chart=Chart(
                    categories=["Maple", "Oak"],
                    series=[("Occupancy", [92, 100])],
                    kind="column",
                    title="Occupancy %",
                ),
            )
        ],
    )
    zf = zipfile.ZipFile(io.BytesIO(gf.data))
    names = zf.namelist()
    assert "ppt/charts/chart1.xml" in names
    chart = zf.read("ppt/charts/chart1.xml").decode()
    assert "<c:barChart>" in chart
    assert "<c:numCache>" in chart  # renders from cached values (no workbook needed)
    slide_rels = zf.read("ppt/slides/_rels/slide1.xml.rels").decode()
    assert "charts/chart1.xml" in slide_rels
    content_types = zf.read("[Content_Types].xml").decode()
    assert "drawingml.chart+xml" in content_types
    # The slide references the chart through a graphic frame.
    assert "graphicFrame" in zf.read("ppt/slides/slide1.xml").decode()


def test_pptx_supports_pie_and_line_charts() -> None:
    gf = build_generated_file(
        file_format="pptx",
        slides=[
            Slide(
                title="Mix",
                chart=Chart(categories=["A", "B"], series=[("s", [70, 30])], kind="pie"),
            ),
            Slide(
                title="Trend",
                chart=Chart(categories=["Jan", "Feb"], series=[("y", [1, 2])], kind="line"),
            ),
        ],
    )
    blob = _ooxml_text(gf.data)
    assert "<c:pieChart>" in blob
    assert "<c:lineChart>" in blob


def test_pptx_derived_table_slide_has_native_table_and_chart() -> None:
    gf = build_generated_file(
        file_format="pptx",
        title="Portfolio",
        columns=["Property", "Units"],
        rows=[["Maple", 12], ["Oak", 8]],
    )
    zf = zipfile.ZipFile(io.BytesIO(gf.data))
    blob = _ooxml_text(gf.data)
    # A native table (a:tbl) rather than bullet text, plus an auto-derived chart.
    assert "<a:tbl>" in blob
    assert "ppt/charts/chart1.xml" in zf.namelist()


def test_pptx_chart_without_series_raises() -> None:
    with pytest.raises(ArtifactError):
        build_generated_file(
            file_format="pptx",
            slides=[Slide(title="Empty", chart=Chart(categories=["A"], series=[]))],
        )


def test_docx_has_styles_and_shaded_table() -> None:
    gf = build_generated_file(
        file_format="docx",
        title="Property Report",
        sections=SECTIONS,
        columns=COLUMNS,
        rows=ROWS,
    )
    zf = zipfile.ZipFile(io.BytesIO(gf.data))
    names = zf.namelist()
    assert "word/styles.xml" in names
    document = zf.read("word/document.xml").decode()
    assert 'w:pStyle w:val="Title"' in document
    assert 'w:pStyle w:val="Heading1"' in document
    # Header row is shaded with the brand accent colour.
    assert 'w:fill="2F5597"' in document
    content_types = zf.read("[Content_Types].xml").decode()
    assert "wordprocessingml.styles+xml" in content_types


def test_pdf_is_styled_with_colour_and_bold_font() -> None:
    gf = build_generated_file(
        file_format="pdf",
        title="Report",
        sections=SECTIONS,
        columns=COLUMNS,
        rows=ROWS,
    )
    assert gf.data.startswith(b"%PDF-1.4")
    # A bold font resource and colour operators indicate the styled layout.
    assert b"Helvetica-Bold" in gf.data
    assert b" rg" in gf.data  # fill-colour operator used for headings/shading


def test_unsupported_format_raises() -> None:
    with pytest.raises(ArtifactError):
        build_generated_file(file_format="rtf", columns=COLUMNS, rows=ROWS)


def test_empty_content_raises() -> None:
    with pytest.raises(ArtifactError):
        build_generated_file(file_format="csv")
    with pytest.raises(ArtifactError):
        build_generated_file(file_format="pdf")


def test_filename_is_sanitized_and_extension_normalized() -> None:
    gf = build_generated_file(
        file_format="csv",
        filename="../../etc/pass wd.txt",
        columns=COLUMNS,
        rows=ROWS,
    )
    assert "/" not in gf.name
    assert gf.name.endswith(".csv")
    assert gf.name == "pass wd.csv"


def test_oversize_result_raises(monkeypatch) -> None:
    # Force a tiny cap so a normal file trips the guard.
    monkeypatch.setattr("mcp_server_buildium.llm.artifacts.MAX_ARTIFACT_BYTES", 1)
    with pytest.raises(ArtifactError):
        build_generated_file(file_format="csv", columns=COLUMNS, rows=ROWS)


def test_max_artifact_bytes_is_positive() -> None:
    assert MAX_ARTIFACT_BYTES > 0


def test_current_artifacts_registry_roundtrip() -> None:
    token = set_current_artifacts()
    try:
        assert get_current_artifacts() == []
        gf = build_generated_file(file_format="csv", columns=COLUMNS, rows=ROWS)
        add_current_artifact(gf)
        registered = get_current_artifacts()
        assert len(registered) == 1
        assert registered[0].name == gf.name
        event = registered[0].to_event()
        assert event["type"] == "artifact"
        assert event["media_type"] == "text/csv"
        assert event["data"]  # base64 payload present
    finally:
        current_artifacts.reset(token)
    # Reset restores the empty default outside the request scope.
    assert get_current_artifacts() == []


def _read_csv_cells(data: bytes) -> list[list[str]]:
    """Decode a generated CSV artifact (stripping the UTF-8 BOM) into rows."""
    text = data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_csv_neutralizes_formula_injection() -> None:
    # Cells that a spreadsheet would evaluate as formulas must be prefixed with
    # a single quote so they render as literal text (CSV/formula injection).
    gf = build_generated_file(
        file_format="csv",
        columns=["Name", "Note"],
        rows=[
            ["=1+1", "+SUM(A1:A9)"],
            ["-2+3", "@cmd"],
            ["safe", "normal text"],
        ],
    )
    cells = _read_csv_cells(gf.data)
    assert cells[1] == ["'=1+1", "'+SUM(A1:A9)"]
    assert cells[2] == ["'-2+3", "'@cmd"]
    # Benign values are left untouched.
    assert cells[3] == ["safe", "normal text"]


def test_csv_neutralizes_formula_injection_in_headers() -> None:
    gf = build_generated_file(
        file_format="csv",
        columns=["=danger", "ok"],
        rows=[["a", "b"]],
    )
    cells = _read_csv_cells(gf.data)
    assert cells[0] == ["'=danger", "ok"]
