"""
Shared helpers for the tools: error mapping and turning a stored document
into MCP content
"""

from contextlib import contextmanager
from typing import Any, Iterator

from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import File
from mcp_types import TextContent

from .config import PandocConfig
from .documents import StoredDocument
from .runner import ConversionError

# (marker in pandoc's error output, hint); the first match is shown
LATEX_HINTS = [
    ("openin_any", "The input tries to read a file outside its own directory (e.g. raw LaTeX "
                   "\\input or \\include), which this server does not allow."),
    ("select a different --pdf-engine", "The PDF engine is not installed on the server."),
    (".sty' not found", "The document needs a LaTeX package that is not installed on the server."),
    ("Undefined control sequence", "The input contains LaTeX the engine does not understand."),
    ("Unicode character", "Try a defaults file that uses xelatex with a font covering the text."),
]


@contextmanager
def tool_errors() -> Iterator[None]:
    """Report bad input and pandoc failures to the client as tool errors"""
    try:
        yield
    except ValueError as e:
        raise ToolError(str(e)) from e
    except ConversionError as e:
        message = f"Pandoc failed: {_trim(str(e))}"
        hint = next((hint for marker, hint in LATEX_HINTS if marker in str(e)), None)
        raise ToolError(f"{message} {hint}" if hint else message) from e
    except TimeoutError as e:
        raise ToolError(f"{e}. Try a smaller document or a simpler output format.") from e


def document_result(
    config: PandocConfig,
    document: StoredDocument,
    data: bytes,
    text: str | None,
    summary: list[str],
    structured: dict[str, Any],
    inline: bool,
) -> ToolResult:
    """Summary text, then the document inline (as text, or as an embedded file) if allowed"""
    lines = list(summary)
    content: list[Any] = []

    if not inline:
        lines.append("(Not returned inline; download it from the URL.)")
    elif text is not None and len(text) <= config.max_inline_chars:
        content.append(TextContent(type="text", text=text))
    elif text is None and len(data) <= config.max_inline_bytes:
        content.append(File(data=data, name=document.filename)
                       .to_resource_content(mime_type=document.mime_type))
        lines.append(f"The file is attached as an embedded resource ({document.mime_type}).")
    else:
        lines.append("(Too large to return inline; download it from the URL.)")

    return ToolResult(
        content=[TextContent(type="text", text="\n".join(lines)), *content],
        structured_content={
            "id": document.id,
            "url": document.url,
            "filename": document.filename,
            "mime_type": document.mime_type,
            "size": document.size,
            **structured,
        },
    )


def _trim(message: str, limit: int = 2000) -> str:
    return message if len(message) <= limit else "..." + message[-limit:]
