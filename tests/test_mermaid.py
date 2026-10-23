import base64
import io
import re
import zipfile

import pytest
from fastmcp import Client

from pandoc_mcp.server import PandocMCPServer
from pandoc_mcp.service import PandocMCPService

from .conftest import requires_mermaid, requires_pandoc, requires_xelatex

pytestmark = [requires_pandoc, requires_mermaid]

DIAGRAMS = """# Flow

```mermaid
graph LR
  A[Start] --> B{Ok?}
```

``` {.mermaid caption="The *sequence*" width="60%"}
sequenceDiagram
  Alice->>Bob: Hello
```
"""


@pytest.fixture
def server(app_config):
    server = PandocMCPServer(app_config.server, PandocMCPService(app_config))
    server.service.initialize()
    return server


async def convert(server, **args):
    async with Client(server.create_mcp_app()) as client:
        return await client.call_tool("convert_document", args)


def blob(result) -> bytes:
    return base64.b64decode(result.content[1].resource.blob)


@pytest.mark.parametrize("sandbox", [False, True])
async def test_docx_embeds_png_diagrams_with_caption(server, sandbox):
    server.service.pandoc.sandbox = sandbox
    result = await convert(server, contents=DIAGRAMS, output_format="docx")
    with zipfile.ZipFile(io.BytesIO(blob(result))) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        document = z.read("word/document.xml").decode()
    assert len(media) == 2 and all(n.endswith(".png") for n in media)
    assert "sequence" in document and "graph LR" not in document
    assert result.structured_content["warnings"] == []


async def test_html_gets_inline_svgs_with_distinct_ids(server):
    result = await convert(server, contents=DIAGRAMS, output_format="html")
    html = result.content[1].text
    assert re.findall(r'<svg[^>]* id="(mermaid-\d)"', html) == ["mermaid-1", "mermaid-2"]
    assert "<figure" in html and 'style="width:60%"' in html


async def test_markdown_output_keeps_the_code(server):
    result = await convert(server, contents=DIAGRAMS, output_format="gfm")
    assert "``` mermaid" in result.content[1].text or "```mermaid" in result.content[1].text


async def test_disabled_per_call(server):
    result = await convert(server, contents=DIAGRAMS, output_format="html", render_mermaid=False)
    html = result.content[1].text
    assert 'id="mermaid-' not in html and 'class="sourceCode mermaid"' in html


async def test_syntax_error_is_a_warning_and_keeps_code(server):
    result = await convert(server, contents="```mermaid\ngraph LR\n  A -->\n```\n",
                           output_format="html", standalone=False)
    assert 'class="sourceCode mermaid"' in result.content[1].text
    assert any("Mermaid diagram 1" in w for w in result.structured_content["warnings"])


@requires_xelatex
async def test_pdf_gets_vector_diagrams(server):
    server.service.pandoc.sandbox = True
    result = await convert(server, contents=DIAGRAMS, output_format="pdf")
    assert blob(result)[:4] == b"%PDF" and result.structured_content["warnings"] == []
