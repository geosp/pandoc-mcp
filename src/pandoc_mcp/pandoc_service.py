"""
Document conversion: resolve the input, run pandoc, store the output

Inputs come inline (text, or base64 for binary formats), as the id of a
stored document, or, when local paths are allowed, as a file path. Every
output is stored and gets an id and URL; the tool layer decides how much of
it to return inline.
"""

import base64
import binascii
import logging
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assets import AssetLibrary
from .config import PandocConfig
from .documents import DocumentStore, StoredDocument, safe_filename
from .runner import (
    INPUT_BY_EXTENSION,
    PandocRunner,
    base_format,
    extension_for,
    is_binary,
)
from .uploads import UploadLinks

logger = logging.getLogger(__name__)

DOCUMENT_REF = re.compile(r"(?:^|/documents/)([0-9a-f]{32})(?:/|$)")
LATEX_ENGINES = {"xelatex", "pdflatex", "lualatex", "latexmk", "tectonic"}
REFERENCE_DOC_FORMATS = {"docx", "odt", "pptx"}
PURGE_INTERVAL_S = 3600
MERMAID_FILTER = Path(__file__).parent / "filters" / "mermaid.lua"
RESOURCES_FILTER = Path(__file__).parent / "filters" / "resources.lua"
MAX_RESOURCES = 20
MERMAID_FENCE = re.compile(rb"^\s*(```|~~~)\s*\{?\s*\.?mermaid\b", re.MULTILINE)


@dataclass(frozen=True)
class ConversionResult:
    document: StoredDocument
    data: bytes
    output_format: str
    warnings: list[str] = field(default_factory=list)
    saved_to: Path | None = None

    @property
    def text(self) -> str | None:
        """The output as text, or None for binary formats"""
        return None if is_binary(self.output_format) else self.data.decode("utf-8", "replace")


@dataclass(frozen=True)
class _Input:
    data: bytes
    name: str
    format: str | None
    source: str
    resource_dir: Path | None = None


class PandocService:
    def __init__(self, config: PandocConfig, runner: PandocRunner, store: DocumentStore,
                 assets: AssetLibrary, local_paths: bool, sandbox: bool):
        self.config = config
        self.runner = runner
        self.store = store
        self.assets = assets
        self.local_paths = local_paths
        self.sandbox = sandbox
        self._last_purge = 0.0
        self.mermaid_env: dict[str, str] | None = None
        """Environment for the mermaid filter; None when mermaid rendering is unavailable"""
        self.uploads: UploadLinks | None = None
        """Upload links; set by the MCP service"""

    async def convert(
        self,
        output_format: str,
        *,
        contents: str | None = None,
        contents_base64: str | None = None,
        document: str | None = None,
        input_path: str | None = None,
        input_format: str | None = None,
        filename: str | None = None,
        output_path: str | None = None,
        standalone: bool = True,
        defaults: str | None = None,
        reference_doc: str | None = None,
        template: str | None = None,
        filters: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        table_of_contents: bool = False,
        render_mermaid: bool = True,
        resources: list[str] | None = None,
    ) -> ConversionResult:
        self._purge_if_due()
        source = self._read_input(contents, contents_base64, document, input_path, filename)
        extra_files = self._read_resources(resources or [], len(source.data))
        input_format = input_format or source.format
        if not input_format:
            raise ValueError("Cannot tell the input format: pass input_format, or a filename "
                             "with a known extension (e.g. report.docx)")
        output_format = output_format.strip()
        self.runner.check_formats(input_format, output_format)
        pdf = base_format(output_format) == "pdf"

        out_name = f"{Path(safe_filename(source.name)).stem or 'document'}." \
                   f"{extension_for(output_format)}"
        used_assets: dict[str, Any] = {}

        with tempfile.TemporaryDirectory(prefix="pandoc-mcp-") as tmp:
            workdir = Path(tmp)
            input_file = workdir / f"input.{_input_extension(input_format)}"
            input_file.write_bytes(source.data)
            output_file = workdir / "out" / out_name
            output_file.parent.mkdir()
            resources_dir = workdir / "resources"
            resources_dir.mkdir()
            for name, file_data in extra_files.items():
                (resources_dir / name).write_bytes(file_data)

            # Defaults first: later command-line options override the defaults file,
            # so it cannot change the requested formats or output file
            args: list[str] = []
            if defaults:
                asset = self.assets.resolve("defaults", defaults)
                args.append(f"--defaults={asset.path}")
                used_assets["defaults"] = asset.name
            args += ["--from", input_format, "--output", str(output_file)]
            if pdf:
                args.append(f"--pdf-engine={self.config.pdf_engine}")
            else:
                args += ["--to", output_format]
            if standalone:
                args.append("--standalone")
            if table_of_contents:
                args.append("--toc")
            if self.sandbox:
                args.append("--sandbox")
            if source.resource_dir:
                args.append(f"--resource-path={source.resource_dir}")

            if pdf and not defaults and self.config.pdf_engine in LATEX_ENGINES:
                args += ["--variable", "geometry:margin=1in"]
            if reference_doc:
                if base_format(output_format) not in REFERENCE_DOC_FORMATS:
                    raise ValueError("reference_doc only applies to docx, odt and pptx output")
                asset = self.assets.resolve("reference-docs", reference_doc)
                args.append(f"--reference-doc={asset.path}")
                used_assets["reference_doc"] = asset.name
            if template:
                asset = self.assets.resolve("templates", template)
                args.append(f"--template={asset.path}")
                used_assets["template"] = asset.name
            # Bundled filters run before the user's, so those see images and diagrams
            filter_env: dict[str, str] = {}
            if extra_files:
                args.append(f"--lua-filter={RESOURCES_FILTER}")
                filter_env["PANDOC_MCP_RESOURCES_DIR"] = str(resources_dir)
                used_assets["resources"] = sorted(extra_files)
            mermaid = (render_mermaid and self.mermaid_env is not None
                       and MERMAID_FENCE.search(source.data) is not None)
            if mermaid:
                args.append(f"--lua-filter={MERMAID_FILTER}")
                used_assets["mermaid"] = True
                filter_env.update(self.mermaid_env)
            for name in filters or []:
                asset = self.assets.resolve("filters", name)
                flag = "--lua-filter" if asset.path.suffix == ".lua" else "--filter"
                args.append(f"{flag}={asset.path}")
                used_assets.setdefault("filters", []).append(asset.name)
            for key, value in (metadata or {}).items():
                if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                    raise ValueError(f"Invalid metadata key: '{key}'")
                args += ["--metadata", f"{key}={value}"]

            args.append(input_file.name)
            run = await self.runner.run(args, cwd=workdir, env=filter_env)
            data = output_file.read_bytes()

        warnings = [line for line in run.stderr.splitlines() if line.strip()]
        stored = self.store.save(data, out_name, {
            "tool": "convert_document",
            "source": source.source,
            "input_format": input_format,
            "output_format": output_format,
            **used_assets,
            "warnings": warnings,
        })
        logger.info(f"Converted {source.source} {input_format} -> {output_format} "
                    f"as {stored.id} ({stored.size} bytes)")

        saved_to = self._write_local(output_path, data) if output_path else None
        return ConversionResult(stored, data, output_format, warnings, saved_to)

    def store_upload(self, data: bytes, filename: str, upload_id: str) -> StoredDocument:
        name = safe_filename(filename)
        guessed = INPUT_BY_EXTENSION.get(Path(name).suffix.removeprefix(".").lower())
        return self.store.save(data, name, {"tool": "upload", "format": guessed,
                                            "upload_id": upload_id})

    def _read_resources(self, refs: list[str], input_size: int) -> dict[str, bytes]:
        """Stored documents to place next to the input, by file name"""
        if len(refs) > MAX_RESOURCES:
            raise ValueError(f"At most {MAX_RESOURCES} resources per conversion")
        files: dict[str, bytes] = {}
        total = input_size
        for ref in refs:
            stored = self.store.get(_document_id(ref))
            if stored is None:
                raise ValueError(f"No stored document '{ref}' to use as a resource")
            if stored.filename in files:
                raise ValueError(f"Two resources are named '{stored.filename}'; "
                                 "upload one under a different name")
            total += stored.size
            if total > self.config.max_input_bytes:
                raise ValueError(f"Input and resources together are larger than "
                                 f"{self.config.max_input_bytes} bytes")
            files[stored.filename] = stored.path.read_bytes()
        return files

    def get_document(self, document: str | None) -> tuple[StoredDocument, dict[str, Any], bytes]:
        if document:
            stored = self.store.get(_document_id(document))
            if stored is None:
                raise ValueError(f"No stored document '{document}' (documents are kept for "
                                 f"{self.config.retention_days:g} days)")
        else:
            stored = self.store.latest()
            if stored is None:
                raise ValueError("No documents stored yet")
        return stored, self.store.metadata(stored), stored.path.read_bytes()

    def _read_input(self, contents: str | None, contents_base64: str | None,
                    document: str | None, input_path: str | None, filename: str | None) -> _Input:
        given = [name for name, value in (("contents", contents),
                                          ("contents_base64", contents_base64),
                                          ("document", document),
                                          ("input_path", input_path)) if value is not None]
        if len(given) != 1:
            raise ValueError("Pass exactly one of contents, contents_base64, document or "
                             f"input_path (got {', '.join(given) or 'none'})")
        guessed = _format_from_name(filename) if filename else None

        if contents is not None:
            data = contents.encode("utf-8")
            source = _Input(data, filename or "document", guessed or "markdown", "inline text")
        elif contents_base64 is not None:
            try:
                data = base64.b64decode(re.sub(r"\s+", "", contents_base64), validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValueError(f"contents_base64 is not valid base64: {e}") from e
            source = _Input(data, filename or "document", guessed, "inline base64")
        elif document is not None:
            stored, meta, data = self.get_document(document)
            fmt = guessed or meta.get("format") or meta.get("output_format") \
                or _format_from_name(stored.filename)
            if fmt and base_format(fmt) == "pdf":
                raise ValueError("Pandoc cannot read PDF documents")
            source = _Input(data, filename or stored.filename, fmt, f"document {stored.id}")
        else:
            if not self.local_paths:
                raise ValueError("input_path is not available on this server: pass contents, "
                                 "contents_base64 or a document id")
            path = Path(input_path).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"Input file not found: {input_path}")
            if path.stat().st_size > self.config.max_input_bytes:
                raise ValueError(f"Input is larger than {self.config.max_input_bytes} bytes")
            source = _Input(path.read_bytes(), filename or path.name,
                            guessed or _format_from_name(path.name), str(path), path.parent)

        if len(source.data) > self.config.max_input_bytes:
            raise ValueError(f"Input is larger than {self.config.max_input_bytes} bytes")
        if not source.data:
            raise ValueError("Input is empty")
        return source

    def _write_local(self, output_path: str, data: bytes) -> Path:
        if not self.local_paths:
            raise ValueError("output_path is not available on this server: use the returned "
                             "document URL or inline content")
        path = Path(output_path).expanduser().resolve()
        if path.is_dir():
            raise ValueError(f"output_path must include a file name: {output_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _purge_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_purge >= PURGE_INTERVAL_S:
            self._last_purge = now
            removed = self.store.purge(self.config.retention_days)
            if removed:
                logger.info(f"Removed {removed} expired document(s)")


def _document_id(ref: str) -> str:
    match = DOCUMENT_REF.search(ref.strip())
    if not match:
        raise ValueError(f"Not a document id or URL from this server: '{ref}'")
    return match.group(1)


def _format_from_name(name: str) -> str | None:
    return INPUT_BY_EXTENSION.get(Path(name).suffix.removeprefix(".").lower())


def _input_extension(input_format: str) -> str:
    base = base_format(input_format)
    for extension, fmt in INPUT_BY_EXTENSION.items():
        if fmt == base:
            return extension
    return "txt"
