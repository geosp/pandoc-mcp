"""
MCP Tool: Get Document
"""

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from pydantic import Field

from ...pandoc_service import PandocService
from ...results import document_result, tool_errors
from ...runner import TEXT_EXTENSIONS


def register_tool(mcp: FastMCP, pandoc: PandocService) -> None:
    @mcp.tool()
    async def get_document(
        document: Annotated[
            str | None,
            Field(description="Document id or URL from this server; leave empty for the latest"),
        ] = None,
        inline: Annotated[
            bool,
            Field(description="Return the content in this response (text, or an embedded "
                              "base64 file)"),
        ] = True,
    ) -> ToolResult:
        """
        Fetch a stored document again, with how it was produced

        Returns its id, URL and metadata (source, formats, assets, warnings), and its content
        inline when small enough. Use it to retrieve an uploaded file or an earlier result.
        """
        with tool_errors():
            stored, metadata, data = pandoc.get_document(document)
        text = data.decode("utf-8", "replace") if stored.extension in TEXT_EXTENSIONS else None
        summary = [f"Document {stored.id}: {stored.filename} ({stored.size} bytes)",
                   f"Download: {stored.url}"]
        summary += [f"{key}: {value}" for key, value in metadata.items() if value]
        return document_result(pandoc.config, stored, data, text, summary,
                               {"metadata": metadata}, inline)
