"""
MCP Tool: List Assets
"""

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

from ...pandoc_service import PandocService


def register_tool(mcp: FastMCP, pandoc: PandocService) -> None:
    @mcp.tool()
    async def list_assets() -> ToolResult:
        """
        List the filters, defaults files, reference documents and templates on this server

        Pass their names to convert_document (filters, defaults, reference_doc, template).
        Also reports the pandoc version and PDF engine.
        """
        info = pandoc.runner.info
        catalog = pandoc.assets.all()
        mermaid = pandoc.mermaid_env is not None
        lines = [f"Pandoc {info.version if info else 'unknown'}, "
                 f"PDF engine {pandoc.config.pdf_engine}, "
                 f"mermaid diagrams {'enabled' if mermaid else 'unavailable'}"]
        for kind, assets in catalog.items():
            lines.append(f"\n{kind}:")
            lines += [f"- {a.name}" + (f": {a.description}" if a.description else "")
                      for a in assets] or ["(none)"]
        return ToolResult(
            content="\n".join(lines),
            structured_content={
                "pandoc_version": info.version if info else None,
                "pdf_engine": pandoc.config.pdf_engine,
                "mermaid": mermaid,
                **{kind.replace("-", "_"): [{"name": a.name, "description": a.description}
                                            for a in assets]
                   for kind, assets in catalog.items()},
            },
        )
