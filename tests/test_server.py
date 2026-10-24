import base64
import contextlib
import io
import struct
import zipfile
import zlib

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


def blob(result) -> bytes:
    return base64.b64decode(result.content[1].resource.blob)


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode()


async def test_tools_and_instructions(server):
    async with Client(server.create_mcp_app()) as client:
        names = {t.name for t in await client.list_tools()}
        assert names == {"convert_document", "get_document", "list_assets", "check_upload",
                         "create_upload_link"}
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


def png_bytes() -> bytes:
    """A 2x2 red PNG, built without image libraries"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@contextlib.asynccontextmanager
async def http_server(app_config, **config):
    """An HTTP-mode server with an MCP client and a plain HTTP client on its ASGI app"""
    for key, value in config.items():
        setattr(app_config.pandoc, key, value)
    server = make_server(app_config, mode="http", token="secret")
    app = server.create_asgi_app()
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://pandoc.test:8301"
    ) as http:
        yield server, Client(server._mcp_app), http


async def test_http_requires_token(app_config):
    async with http_server(app_config) as (_, _, http):
        assert (await http.post("/mcp", json={})).status_code == 401
        # the old master-token upload route is gone
        response = await http.post("/upload", files={"file": ("a.md", b"# Hi")},
                                   headers={"Authorization": "Bearer secret"})
        assert response.status_code == 404


async def test_upload_link_flow(app_config):
    async with http_server(app_config) as (server, client, http):
        async with client:
            link = (await client.call_tool("create_upload_link", {"max_files": 2}))
            url, upload_id = link.structured_content["upload_url"], link.structured_content["upload_id"]
            assert url.startswith("http://pandoc.test:8301/upload/") and upload_id not in url

            empty = await client.call_tool("check_upload", {"upload_id": upload_id})
            assert empty.structured_content == {"files": [], "remaining": 2, "expired": False}

            assert (await http.get(url)).status_code == 200
            uploaded = await http.post(url, files=[("file", ("../notes.md", MARKDOWN.encode())),
                                                   ("file", ("chart.png", png_bytes()))])
            assert uploaded.status_code == 200
            assert uploaded.json()["remaining"] == 0
            assert "id" not in uploaded.json()["files"][0]

            # used up: the link no longer works, even for its form
            assert (await http.post(url, files={"file": ("x.md", b"x")})).status_code == 404
            assert (await http.get(url)).status_code == 404

            checked = await client.call_tool("check_upload", {"upload_id": upload_id})
            files = {f["filename"]: f for f in checked.structured_content["files"]}
            assert set(files) == {"notes.md", "chart.png"}

            converted = await client.call_tool("convert_document", {
                "contents": "# Report\n\n![Chart](chart.png)\n", "output_format": "docx",
                "resources": [files["chart.png"]["id"]]})
            with zipfile.ZipFile(io.BytesIO(blob(converted))) as z:
                assert any(n.startswith("word/media/") for n in z.namelist())

            download = await http.get(files["notes.md"]["url"].removeprefix("http://pandoc.test:8301"))
            assert download.text == MARKDOWN


async def test_upload_link_expires_and_limits(app_config):
    async with http_server(app_config, upload_link_max_files=3) as (server, client, http):
        async with client:
            with pytest.raises(ToolError, match="at most 3"):
                await client.call_tool("create_upload_link", {"max_files": 4})
            link = await client.call_tool("create_upload_link", {"max_files": 1})
            url = link.structured_content["upload_url"]
            too_many = await http.post(url, files=[("file", ("a.md", b"a")), ("file", ("b.md", b"b"))])
            assert too_many.status_code == 400
            assert (await http.post(url + "x", files={"file": ("a.md", b"a")})).status_code == 404

            server.service.pandoc.uploads.clock = lambda: float("inf")
            assert (await http.post(url, files={"file": ("a.md", b"a")})).status_code == 404


async def test_upload_link_needs_http(server):
    async with Client(server.create_mcp_app()) as client:
        with pytest.raises(ToolError, match="input_path instead"):
            await client.call_tool("create_upload_link", {})


@pytest.mark.parametrize("sandbox", [False, True])
@pytest.mark.parametrize("output_format, embedded", [
    ("html", "data:image/png;base64,"),
    ("docx", "word/media/"),
])
async def test_resources_embed_images(server, sandbox, output_format, embedded):
    server.service.pandoc.sandbox = sandbox
    image = server.service.pandoc.store.save(png_bytes(), "chart.png")
    async with Client(server.create_mcp_app()) as client:
        result = await client.call_tool("convert_document", {
            "contents": "![Chart](./chart.png)\n", "output_format": output_format,
            "resources": [image.url]})
    if output_format == "html":
        assert embedded in result.content[1].text
    else:
        with zipfile.ZipFile(io.BytesIO(blob(result))) as z:
            assert any(n.startswith(embedded) for n in z.namelist())
    assert result.structured_content["warnings"] == []


async def test_resources_reject_duplicates(server):
    store = server.service.pandoc.store
    first, second = store.save(png_bytes(), "chart.png"), store.save(png_bytes(), "chart.png")
    async with Client(server.create_mcp_app()) as client:
        with pytest.raises(ToolError, match="Two resources are named 'chart.png'"):
            await client.call_tool("convert_document", {
                "contents": "x", "output_format": "html", "resources": [first.id, second.id]})
