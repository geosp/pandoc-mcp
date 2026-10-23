import os
import time

import pytest
from fastmcp.exceptions import ToolError

from pandoc_mcp.assets import AssetLibrary
from pandoc_mcp.documents import DocumentStore, safe_filename
from pandoc_mcp.results import tool_errors
from pandoc_mcp.runner import ConversionError, base_format, extension_for, is_binary, mime_type_for

from .conftest import ASSETS_DIR


def test_format_helpers():
    assert base_format("markdown+smart-raw_tex") == "markdown"
    assert extension_for("gfm") == "md" and extension_for("docx") == "docx"
    assert extension_for("html5+smart") == "html" and extension_for("plain") == "txt"
    assert is_binary("docx") and is_binary("pdf") and not is_binary("html")
    assert mime_type_for("pdf") == "application/pdf" and mime_type_for("rst") == "text/plain"


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\x\\report.docx") == "report.docx"
    assert safe_filename("a;rm -rf.md") == "a_rm -rf.md"
    assert safe_filename("..") == "document"


def test_assets_resolve_by_name_only():
    assets = AssetLibrary(ASSETS_DIR)
    assert assets.resolve("defaults", "academic-pdf").name == "academic-pdf.yaml"
    assert assets.resolve("filters", "pagebreak.lua").path.suffix == ".lua"
    assert assets.resolve("defaults", "academic-pdf.yaml").description.startswith("Academic")
    for bad in ("../defaults/academic-pdf.yaml", "/etc/passwd", ".hidden", ""):
        with pytest.raises(ValueError):
            assets.resolve("defaults", bad)
    with pytest.raises(ValueError, match="available: academic-pdf.yaml"):
        assets.resolve("defaults", "missing")


def test_non_executable_json_filter_is_ignored(tmp_path):
    (tmp_path / "filters").mkdir()
    script = tmp_path / "filters" / "tweak.py"
    script.write_text("#!/usr/bin/env python3\n")
    assets = AssetLibrary(tmp_path)
    assert assets.of_kind("filters") == []
    os.chmod(script, 0o755)
    assert [a.name for a in assets.of_kind("filters")] == ["tweak.py"]


def test_document_store_roundtrip_and_purge(tmp_path):
    store = DocumentStore(tmp_path, "http://h:1/")
    doc = store.save(b"hello", "../x/notes.md", {"format": "markdown"})
    assert doc.filename == "notes.md" and doc.url == f"http://h:1/documents/{doc.id}/notes.md"
    assert store.get(doc.id).path.read_bytes() == b"hello"
    assert store.metadata(doc)["format"] == "markdown"
    assert store.path_for(doc.id, "notes.md") == doc.path
    assert store.path_for(doc.id, "other.md") is None
    assert store.get("../" + doc.id) is None
    assert store.latest().id == doc.id

    old = time.time() - 10 * 86400
    os.utime(doc.path.parent, (old, old))
    assert store.purge(7) == 1 and store.get(doc.id) is None


@pytest.mark.parametrize("stderr, hint", [
    ("xelatex: Not reading from /etc/passwd (openin_any = p)\n! LaTeX Error: File not found",
     "outside its own directory"),
    ("xelatex not found. Please select a different --pdf-engine or install xelatex",
     "PDF engine is not installed"),
    ("! LaTeX Error: File `fancy.sty' not found.", "LaTeX package"),
    ("something else", None),
])
def test_conversion_error_hints(stderr, hint):
    with pytest.raises(ToolError) as raised:
        with tool_errors():
            raise ConversionError(stderr)
    message = str(raised.value)
    assert message.startswith("Pandoc failed:")
    if hint:
        assert hint in message
    else:
        assert message == f"Pandoc failed: {stderr}"
