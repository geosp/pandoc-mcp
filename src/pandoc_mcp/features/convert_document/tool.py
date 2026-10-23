"""
MCP Tool: Convert Document
"""

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from pydantic import Field

from ...pandoc_service import PandocService
from ...results import document_result, tool_errors


def register_tool(mcp: FastMCP, pandoc: PandocService) -> None:
    @mcp.tool()
    async def convert_document(
        output_format: Annotated[
            str,
            Field(description="Target format: markdown, gfm, html, docx, pdf, odt, epub, pptx, "
                              "latex, rst, typst, asciidoc, plain, ipynb, ... Pandoc extensions "
                              "are allowed (e.g. 'markdown+smart')."),
        ],
        contents: Annotated[
            str | None,
            Field(description="Text to convert, e.g. markdown you wrote. Its format is "
                              "input_format (default markdown)."),
        ] = None,
        contents_base64: Annotated[
            str | None,
            Field(description="A binary file to convert (docx, odt, epub, pptx, xlsx, ...), "
                              "base64-encoded. Also pass filename or input_format."),
        ] = None,
        document: Annotated[
            str | None,
            Field(description="Id (or URL) of a document stored on this server: a previous "
                              "result or an upload"),
        ] = None,
        input_path: Annotated[
            str | None,
            Field(description="Path of a file on the server's filesystem (local stdio servers "
                              "only)"),
        ] = None,
        input_format: Annotated[
            str | None,
            Field(description="Source format; guessed from filename or the stored document "
                              "when omitted, otherwise markdown for contents"),
        ] = None,
        filename: Annotated[
            str | None,
            Field(description="Name for the input; its stem names the output "
                              "(e.g. 'report.docx' -> report.pdf)"),
        ] = None,
        output_path: Annotated[
            str | None,
            Field(description="Also write the result to this path (local stdio servers only)"),
        ] = None,
        standalone: Annotated[
            bool,
            Field(description="Produce a complete document with header (e.g. <html><head>) "
                              "rather than a fragment"),
        ] = True,
        table_of_contents: Annotated[bool, Field(description="Add a table of contents")] = False,
        defaults: Annotated[
            str | None, Field(description="Name of a defaults file from list_assets")
        ] = None,
        reference_doc: Annotated[
            str | None,
            Field(description="Name of a reference document from list_assets, for docx, odt or "
                              "pptx styling"),
        ] = None,
        template: Annotated[
            str | None, Field(description="Name of a template from list_assets")
        ] = None,
        filters: Annotated[
            list[str] | None,
            Field(description="Names of filters from list_assets, applied in order"),
        ] = None,
        metadata: Annotated[
            dict[str, str] | None,
            Field(description="Document metadata, e.g. {\"title\": \"Q4 Report\", "
                              "\"author\": \"Ana\", \"lang\": \"en\"}"),
        ] = None,
        render_mermaid: Annotated[
            bool,
            Field(description="Render ```mermaid code blocks as diagrams (kept as code in "
                              "markdown output)"),
        ] = True,
        inline: Annotated[
            bool,
            Field(description="Return the result in this response (text, or an embedded "
                              "base64 file). Set false to get only the id and URL."),
        ] = True,
    ) -> ToolResult:
        """
        Convert a document between formats with Pandoc

        Give the input as `contents` (text you generated), `contents_base64` (a binary file),
        or `document` (an id from an earlier result or upload). The result is stored on the
        server and returned with its id and download URL; text output also comes back inline,
        binary output (docx, pdf, ...) as an embedded base64 file when small enough.
        """
        with tool_errors():
            result = await pandoc.convert(
                output_format,
                contents=contents,
                contents_base64=contents_base64,
                document=document,
                input_path=input_path,
                input_format=input_format,
                filename=filename,
                output_path=output_path,
                standalone=standalone,
                defaults=defaults,
                reference_doc=reference_doc,
                template=template,
                filters=filters,
                metadata=metadata,
                table_of_contents=table_of_contents,
                render_mermaid=render_mermaid,
            )
        doc = result.document
        summary = [f"Converted to {result.output_format}: {doc.filename} ({doc.size} bytes), "
                   f"id {doc.id}", f"Download: {doc.url}"]
        if result.saved_to:
            summary.append(f"Saved to: {result.saved_to}")
        if result.warnings:
            summary += ["Pandoc warnings:", *(f"- {w}" for w in result.warnings)]
        return document_result(
            pandoc.config, doc, result.data, result.text, summary,
            {"format": result.output_format, "warnings": result.warnings,
             "saved_to": str(result.saved_to) if result.saved_to else None},
            inline,
        )
