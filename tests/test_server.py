import base64
import io
import zipfile

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import SecretStr

from pandoc_mcp.server import PandocMCPServer
from pandoc_mcp.service import PandocMCPService

from .conftest import requires_pandoc, requires_xelatex

pytestmark = requires_pandoc

MARKDOWN = "# Title\n\nSome *emphasis* and a list:\n\n- one\n- two\n"


def make_server(app_config, mode="stdio", auth=True, token=None):
    app_config.server.transport = mode
    app_config.server.auth_enabled = auth
    app_config.pandoc.token = SecretStr(token) if token else None
    return PandocMCPServer(app_config.server, PandocMCPService(app_config))


@pytest.fixture
def server(app_config):
    server = make_server(app_config)
    server.service.initialize()
    return server


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode()


async def test_tools_and_instructions(server):
    async with Client(server.create_mcp_app()) as client:
        names = {t.name for t in await client.list_tools()}
        assert names == {"convert_document", "get_document", "list_assets"}
        assert "contents_base64" in client.instructions


async def test_markdown_to_html_inline(server):
    async with Client(server.create_mcp_app()) as client:
        result = await client.call_tool("convert_document", {
            "contents": MARKDOWN, "output_format": "html", "standalone": False})
    summary, html = result.content
    assert "http://pandoc.test:8301/documents/" in summary.text
    assert "<em>emphasis</em>" in html.text and "<html" not in html.text
    assert result.structured_content["filename"] == "document.html"


async def test_docx_roundtrip_through_base64(server):
    """The LLM flow: get a docx back inline, send it back as base64, read it as markdown"""
    async with Client(server.create_mcp_app()) as client:
        made = await client.call_tool("convert_document", {
            "contents": MARKDOWN, "output_format": "docx", "filename": "notes.md",
            "reference_doc": "default", "metadata": {"title": "My Notes"}})
        resource = made.content[1].resource
        assert resource.mime_type.endswith("wordprocessingml.document")
        data = base64.b64decode(resource.blob)
        assert "emphasis" in docx_text(data) and "My Notes" in docx_text(data)
        assert made.structured_content["filename"] == "notes.docx"

        back = await client.call_tool("convert_document", {
            "contents_base64": base64.b64encode(data).decode(), "filename": "notes.docx",
            "output_format": "gfm"})
        assert "*emphasis*" in back.content[1].text and "- one" in back.content[1].text

        # chaining by id gives the same result without resending the bytes
        by_id = await client.call_tool("convert_document", {
            "document": made.structured_content["id"], "output_format": "gfm"})
        assert by_id.content[1].text == back.content[1].text


async def test_inline_false_and_get_document(server):
    async with Client(server.create_mcp_app()) as client:
        made = await client.call_tool("convert_document", {
            "contents": MARKDOWN, "output_format": "odt", "inline": False})
        assert len(made.content) == 1 and "Not returned inline" in made.content[0].text
        fetched = await client.call_tool("get_document", {"document": made.structured_content["url"]})
    assert fetched.structured_content["metadata"]["output_format"] == "odt"
    assert fetched.content[1].resource.mime_type == "application/vnd.oasis.opendocument.text"


async def test_filters_and_defaults(server):
    async with Client(server.create_mcp_app()) as client:
        assets = await client.call_tool("list_assets", {})
        assert "pagebreak.lua" in assets.content[0].text
        result = await client.call_tool("convert_document", {
            "contents": "one\n\n\\newpage\n\ntwo\n", "output_format": "html",
            "filters": ["pagebreak"], "defaults": "report-html"})
    html = result.content[1].text
    assert "page-break-after" in html and "<!DOCTYPE html>" in html


@pytest.mark.parametrize("args, message", [
    ({"output_format": "html"}, "exactly one of"),
    ({"contents": "x", "document": "y", "output_format": "html"}, "exactly one of"),
    ({"contents": "x", "output_format": "nonsense"}, "cannot write 'nonsense'"),
    ({"contents": "x", "input_format": "pdf", "output_format": "html"}, "cannot read 'pdf'"),
    ({"contents_base64": "%%%", "filename": "a.docx", "output_format": "md"}, "not valid base64"),
    ({"contents_base64": "aGVsbG8=", "output_format": "html"}, "Cannot tell the input format"),
    ({"contents": "x", "output_format": "html", "filters": ["../../bin/sh"]}, "not a path"),
    ({"contents": "x", "output_format": "html", "reference_doc": "default"}, "only applies"),
    ({"document": "0" * 32, "output_format": "html"}, "No stored document"),
])
async def test_bad_requests_are_tool_errors(server, args, message):
    async with Client(server.create_mcp_app()) as client:
        with pytest.raises(ToolError, match=message):
            await client.call_tool("convert_document", args)


async def test_oversize_input(server):
    server.service.pandoc.config.max_input_bytes = 1024
    async with Client(server.create_mcp_app()) as client:
        with pytest.raises(ToolError, match="larger than 1024"):
            await client.call_tool("convert_document", {
                "contents_base64": base64.b64encode(b"x" * 2000).decode(),
                "filename": "a.md", "output_format": "html"})


async def test_local_paths_in_stdio(server, tmp_path):
    source = tmp_path / "in.md"
    source.write_text(MARKDOWN)
    target = tmp_path / "out" / "result.docx"
    async with Client(server.create_mcp_app()) as client:
        result = await client.call_tool("convert_document", {
            "input_path": str(source), "output_path": str(target), "output_format": "docx"})
    assert target.read_bytes()[:2] == b"PK" and str(target) in result.content[0].text


async def test_local_paths_refused_over_http(app_config, tmp_path):
    server = make_server(app_config, mode="http", auth=False)
    server.service.initialize()
    async with Client(server.create_mcp_app()) as client:
        with pytest.raises(ToolError, match="input_path is not available"):
            await client.call_tool("convert_document", {
                "input_path": str(tmp_path / "x.md"), "output_format": "html"})


@pytest.mark.parametrize("sandbox, embedded", [(False, True), (True, False)])
async def test_sandbox_keeps_server_files_out_of_outputs(server, sandbox, embedded):
    """Unsandboxed, docx output embeds a referenced local file; the sandbox (HTTP default) stops it"""
    server.service.pandoc.sandbox = sandbox
    async with Client(server.create_mcp_app()) as client:
        result = await client.call_tool("convert_document", {
            "contents": "![x](/etc/hostname)\n", "output_format": "docx"})
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result.content[1].resource.blob))) as z:
        assert any(n.startswith("word/media/") for n in z.namelist()) is embedded
    assert any("hostname" in w for w in result.structured_content["warnings"]) is sandbox


@requires_xelatex
async def test_markdown_to_pdf(server):
    async with Client(server.create_mcp_app()) as client:
        result = await client.call_tool("convert_document", {
            "contents": MARKDOWN, "output_format": "pdf", "defaults": "academic-pdf",
            "metadata": {"title": "Paper"}})
    assert base64.b64decode(result.content[1].resource.blob)[:4] == b"%PDF"


async def test_http_auth_and_upload(app_config):
    server = make_server(app_config, mode="http", token="secret")
    app = server.create_asgi_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=transport, base_url="http://pandoc.test"
    ) as http:
        denied = await http.post("/mcp", json={})
        assert denied.status_code == 401
        assert (await http.post("/upload", files={"file": ("a.md", b"# Hi")})).status_code == 401

        uploaded = await http.post("/upload", files={"file": ("../notes.md", MARKDOWN.encode())},
                                   headers={"Authorization": "Bearer secret"})
        assert uploaded.status_code == 200
        body = uploaded.json()
        assert body["filename"] == "notes.md"

        download = await http.get(body["url"].removeprefix("http://pandoc.test:8301"))
        assert download.status_code == 200 and download.text == MARKDOWN
        assert (await http.get(f"/documents/{body['id']}/other.md")).status_code == 404

    result = await server.service.pandoc.convert("html", document=body["id"], standalone=False)
    assert "<em>emphasis</em>" in result.text
