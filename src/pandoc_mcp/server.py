"""
pandoc-mcp: document conversion with Pandoc over MCP

Run it with:
    pandoc-mcp --mode stdio
    pandoc-mcp --mode http --port 8301
"""

import logging

from fastmcp.server.auth import TokenVerifier
from mcp_service_kit import BaseMCPServer, BaseServerConfig, create_main_with_modes

from .auth import BearerTokenVerifier
from .config import AppConfig
from .service import PandocMCPService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
This server converts documents between formats with Pandoc (markdown, html, docx, pdf, odt,
epub, pptx, latex, rst, typst, asciidoc, ipynb and more).

- convert_document takes exactly one input:
  - contents: text, typically markdown you wrote yourself (set input_format for html, latex...)
  - contents_base64: a binary file (docx, odt, epub, pptx, xlsx) as base64, with filename or
    input_format so the format is known
  - document: the id of a document already on the server, to chain conversions without
    sending the bytes again (e.g. docx -> markdown, edit, markdown -> docx)
  - input_path: a file path, only on a local stdio server
- Every result is stored and returned with an id and a download URL. Text output also comes
  back inline; binary output (docx, pdf, ...) comes back as an embedded base64 file when small
  enough. Pass inline=false when you only need the URL, to keep large files out of context.
- Give the user the download URL for files they asked for. On a local stdio server you can
  instead pass output_path to save the file directly.
- PDF output uses LaTeX: write ordinary markdown, and use metadata (title, author, date, lang)
  rather than raw LaTeX for the title block. Pandoc cannot read PDF.
- Diagrams: write Mermaid in ```mermaid code blocks; they are rendered as SVG (HTML), vector
  PDF (PDF/LaTeX) or PNG (docx, pptx, odt, epub). Add {.mermaid caption="..." width="80%"}
  for a captioned, sized figure. A diagram with a syntax error stays as code and the result
  lists the mermaid error under warnings: fix the diagram and convert again.
- Styling: list_assets shows the server's named reference documents (docx/odt/pptx styles),
  defaults files, templates and filters. Pass their names, not paths.
- Images: upload them (see below) and pass their ids as resources; reference them by file
  name in the markdown, e.g. ![Sales](chart.png). Other image paths are not readable.
- Files you cannot pass inline (the user's own files, or files on your shell's filesystem):
  call create_upload_link, then either give the link to the user to open in a browser, or run
  the curl command it shows. Then call check_upload(upload_id, wait_s=60) to get the document
  ids for document or resources. Links are upload-only, expire in minutes and accept a
  limited number of files; never ask the user for the server's token.
- get_document fetches any stored document again.
"""


class PandocMCPServer(BaseMCPServer):
    def __init__(self, config: BaseServerConfig, service: PandocMCPService):
        super().__init__(config, service)
        self.pandoc_config = service.config.pandoc

    @property
    def service_title(self) -> str:
        return "Pandoc MCP Server"

    @property
    def service_description(self) -> str:
        return "Converts documents between formats with Pandoc."

    @property
    def service_version(self) -> str:
        return "0.1.0"

    @property
    def instructions(self) -> str:
        return INSTRUCTIONS

    def create_auth_provider(self) -> TokenVerifier | None:
        token = self.pandoc_config.token
        return BearerTokenVerifier(token) if token is not None else None

    def validate_configuration(self) -> None:
        token = self.pandoc_config.token
        if (
            self.config.transport == "http"
            and self.config.auth_enabled
            and (token is None or not token.get_secret_value())
        ):
            raise ValueError("PANDOC_MCP_TOKEN must be set for HTTP transport (or pass --no-auth)")


main = create_main_with_modes(
    PandocMCPServer,
    PandocMCPService,
    AppConfig,
    "pandoc-mcp",
    "pandoc-mcp - document conversion with Pandoc over MCP",
)


if __name__ == "__main__":
    main()
