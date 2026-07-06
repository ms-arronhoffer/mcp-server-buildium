"""Tests for the document-intake helper tools (describe_create_schema, save)."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from mcp_server_buildium.llm.artifacts import (
    current_artifacts,
    get_current_artifacts,
    set_current_artifacts,
)
from mcp_server_buildium.llm.attachments import (
    Attachment,
    current_attachments,
    set_current_attachments,
)
from mcp_server_buildium.tools._common import list_tools_map
from mcp_server_buildium.tools.documents import register_document_tools


class _StubUploadsApi:
    """Records the upload request and returns a fake S3 ticket."""

    def __init__(self) -> None:
        self.request = None

    async def external_api_files_uploads_create_upload_file_request_async(
        self, *, file_upload_post_message
    ):
        self.request = file_upload_post_message

        class Ticket:
            bucket_url = "https://s3.example.test/bucket"
            form_data = {"key": "abc", "policy": "xyz"}
            physical_file_name = "abc.pdf"

        return Ticket()


class _StubClient:
    def __init__(self) -> None:
        self.files_api = _StubUploadsApi()


def _build_server():
    client = _StubClient()
    mcp = FastMCP("test")
    register_document_tools(mcp, client)
    tools = asyncio.run(list_tools_map(mcp))
    return client, tools


def _structured(result):
    return result.structured_content


def test_describe_create_schema_lists_object_types() -> None:
    _client, tools = _build_server()
    result = asyncio.run(tools["describe_create_schema"].run({"object_type": "list"}))
    data = _structured(result)["data"]
    assert "lease" in data["supported_object_types"]
    assert "rental_tenant" in data["supported_object_types"]


def test_describe_create_schema_returns_fields_for_lease() -> None:
    _client, tools = _build_server()
    result = asyncio.run(tools["describe_create_schema"].run({"object_type": "lease"}))
    data = _structured(result)["data"]
    assert data["object_type"] == "lease"
    assert data["create_tool"] == "create_lease"
    names = {f["name"] for f in data["fields"]}
    # LeasePostMessage requires these core fields.
    assert "LeaseType" in names
    assert data["required_fields"], "expected at least one required field"


def test_describe_create_schema_unknown_type_errors() -> None:
    _client, tools = _build_server()
    result = asyncio.run(tools["describe_create_schema"].run({"object_type": "banana"}))
    err = _structured(result)["error"]
    assert err["code"] == "validation_error"


def test_save_uploaded_document_missing_attachment_errors() -> None:
    _client, tools = _build_server()
    token = set_current_attachments([])
    try:
        result = asyncio.run(
            tools["save_uploaded_document"].run(
                {
                    "file_name": "lease.pdf",
                    "entity_type": "Lease",
                    "entity_id": 5,
                    "category_id": 1,
                }
            )
        )
    finally:
        current_attachments.reset(token)
    err = _structured(result)["error"]
    assert err["code"] == "validation_error"
    assert "no uploaded document" in err["message"].lower()


def test_save_uploaded_document_uploads_bytes(monkeypatch) -> None:
    client, tools = _build_server()

    posted: dict = {}

    class _FakeResponse:
        status_code = 204

    class _FakeAsyncClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, *, files):
            posted["url"] = url
            posted["files"] = files
            return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    att = Attachment("lease.pdf", "application/pdf", b"%PDF-1.4", "JVBERi0xLjQ=")
    token = set_current_attachments([att])
    try:
        result = asyncio.run(
            tools["save_uploaded_document"].run(
                {
                    "file_name": "lease.pdf",
                    "entity_type": "Lease",
                    "entity_id": 42,
                    "category_id": 7,
                    "title": "Signed lease",
                }
            )
        )
    finally:
        current_attachments.reset(token)

    data = _structured(result)["data"]
    assert data["saved"] is True
    assert data["entity_id"] == 42
    # The upload request was built with the right entity + file name.
    req = client.files_api.request
    assert req.entity_type == "Lease"
    assert req.file_name == "lease.pdf"
    # The file bytes were POSTed to the returned storage URL with the form fields.
    assert posted["url"] == "https://s3.example.test/bucket"
    field_names = [f[0] for f in posted["files"]]
    assert "file" in field_names
    assert "key" in field_names


def test_save_uploaded_document_invalid_entity_type_errors() -> None:
    _client, tools = _build_server()
    att = Attachment("lease.pdf", "application/pdf", b"%PDF", "JVBERg==")
    token = set_current_attachments([att])
    try:
        result = asyncio.run(
            tools["save_uploaded_document"].run(
                {
                    "file_name": "lease.pdf",
                    "entity_type": "NotAThing",
                    "entity_id": 1,
                    "category_id": 1,
                }
            )
        )
    finally:
        current_attachments.reset(token)
    err = _structured(result)["error"]
    assert err["code"] == "validation_error"


def test_create_download_file_registers_artifact() -> None:
    _client, tools = _build_server()
    token = set_current_artifacts()
    try:
        result = asyncio.run(
            tools["create_download_file"].run(
                {
                    "file_format": "csv",
                    "filename": "active-leases",
                    "columns": ["Lease", "Rent"],
                    "rows": [[1, 1225], [2, 1400]],
                }
            )
        )
        data = _structured(result)["data"]
        assert data["generated"] is True
        assert data["file_name"] == "active-leases.csv"
        assert data["format"] == "csv"
        # The file was published to the outbound artifact registry.
        artifacts = get_current_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0].name == "active-leases.csv"
        assert b"Rent" in artifacts[0].data
    finally:
        current_artifacts.reset(token)


def test_create_download_file_pptx_from_slides() -> None:
    _client, tools = _build_server()
    token = set_current_artifacts()
    try:
        result = asyncio.run(
            tools["create_download_file"].run(
                {
                    "file_format": "pptx",
                    "title": "Top Properties",
                    "slides": [
                        {"title": "Maple Court", "bullets": ["92% occupancy", "12 units"]},
                    ],
                }
            )
        )
        data = _structured(result)["data"]
        assert data["format"] == "pptx"
        assert data["file_name"].endswith(".pptx")
        assert len(get_current_artifacts()) == 1
    finally:
        current_artifacts.reset(token)


def test_create_download_file_pptx_with_chart_and_layout() -> None:
    _client, tools = _build_server()
    token = set_current_artifacts()
    try:
        result = asyncio.run(
            tools["create_download_file"].run(
                {
                    "file_format": "pptx",
                    "title": "Portfolio Review",
                    "slides": [
                        {"title": "Q3 Review", "subtitle": "Executive summary", "layout": "title"},
                        {
                            "title": "Occupancy",
                            "bullets": ["Maple leads at 92%"],
                            "chart": {
                                "kind": "column",
                                "title": "Occupancy %",
                                "categories": ["Maple", "Oak"],
                                "series": [{"name": "Occupancy", "values": [92, 100]}],
                            },
                        },
                    ],
                }
            )
        )
        data = _structured(result)["data"]
        assert data["format"] == "pptx"
        artifacts = get_current_artifacts()
        assert len(artifacts) == 1
        import io
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(artifacts[0].data))
        assert "ppt/charts/chart1.xml" in zf.namelist()
    finally:
        current_artifacts.reset(token)


def test_create_download_file_pdf_requires_context() -> None:
    """A bare table PDF is rejected so presentation formats stay board-room ready."""
    _client, tools = _build_server()
    token = set_current_artifacts()
    try:
        result = asyncio.run(
            tools["create_download_file"].run(
                {
                    "file_format": "pdf",
                    "title": "Report",
                    "columns": ["Lease", "Rent"],
                    "rows": [[1, 1225]],
                }
            )
        )
        err = _structured(result)["error"]
        assert err["code"] == "validation_error"
        assert "board-room ready" in err["message"]
        assert get_current_artifacts() == []
    finally:
        current_artifacts.reset(token)


def test_create_download_file_pdf_with_description_succeeds() -> None:
    _client, tools = _build_server()
    token = set_current_artifacts()
    try:
        result = asyncio.run(
            tools["create_download_file"].run(
                {
                    "file_format": "pdf",
                    "title": "Report",
                    "description": "Q2 rent roll for the downtown portfolio.",
                    "columns": ["Lease", "Rent"],
                    "rows": [[1, 1225]],
                }
            )
        )
        data = _structured(result)["data"]
        assert data["generated"] is True
        assert data["format"] == "pdf"
        artifacts = get_current_artifacts()
        assert len(artifacts) == 1
        assert b"Q2 rent roll for the downtown portfolio." in artifacts[0].data
    finally:
        current_artifacts.reset(token)


def test_create_download_file_unsupported_format_errors() -> None:
    _client, tools = _build_server()
    token = set_current_artifacts()
    try:
        result = asyncio.run(
            tools["create_download_file"].run(
                {"file_format": "rtf", "columns": ["A"], "rows": [[1]]}
            )
        )
        err = _structured(result)["error"]
        assert err["code"] == "validation_error"
        # Nothing should have been registered for an invalid request.
        assert get_current_artifacts() == []
    finally:
        current_artifacts.reset(token)
