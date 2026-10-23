"""
Running the pandoc executable

Pandoc runs as a subprocess (never through a shell) with a timeout, in a
working directory owned by the caller. The TeX environment variables keep a
LaTeX engine from reading or writing files outside that directory or running
shell commands, so raw LaTeX in an input cannot \\input{/etc/passwd}.
"""

import asyncio
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Output format -> file extension. Formats not listed get ".<format>".
EXTENSIONS = {
    "markdown": "md", "gfm": "md", "commonmark": "md", "commonmark_x": "md",
    "markdown_strict": "md", "markdown_mmd": "md", "markdown_phpextra": "md", "markdown_github": "md",
    "html": "html", "html4": "html", "html5": "html", "chunkedhtml": "zip",
    "revealjs": "html", "slidy": "html", "slideous": "html", "s5": "html", "dzslides": "html",
    "latex": "tex", "beamer": "tex", "context": "tex",
    "plain": "txt", "asciidoc": "adoc", "asciidoctor": "adoc", "mediawiki": "wiki",
    "dokuwiki": "txt", "typst": "typ", "docbook": "xml", "docbook4": "xml", "docbook5": "xml",
    "jats": "xml", "jats_archiving": "xml", "jats_publishing": "xml", "jats_articleauthoring": "xml",
    "tei": "xml", "texinfo": "texi", "man": "1", "csljson": "json", "bibtex": "bib",
    "biblatex": "bib", "epub2": "epub", "epub3": "epub", "native": "hs", "djot": "dj",
}

# Formats whose output is not UTF-8 text
BINARY_FORMATS = {"docx", "odt", "epub", "epub2", "epub3", "pptx", "pdf", "chunkedhtml", "xlsx"}

# File extension -> input format, for guessing when the caller gives none
INPUT_BY_EXTENSION = {
    "md": "markdown", "markdown": "markdown", "txt": "markdown", "html": "html", "htm": "html",
    "tex": "latex", "latex": "latex", "rst": "rst", "docx": "docx", "odt": "odt",
    "epub": "epub", "ipynb": "ipynb", "org": "org", "adoc": "asciidoc", "typ": "typst",
    "rtf": "rtf", "xml": "docbook", "dbk": "docbook", "json": "json", "wiki": "mediawiki",
    "textile": "textile", "xlsx": "xlsx", "pptx": "pptx", "csv": "csv", "tsv": "tsv",
    "bib": "biblatex", "fb2": "fb2", "opml": "opml", "dj": "djot", "1": "man", "t2t": "t2t",
}

MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "odt": "application/vnd.oasis.opendocument.text",
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "html": "text/html",
    "md": "text/markdown",
    "tex": "text/x-tex",
    "json": "application/json",
    "xml": "application/xml",
    "ipynb": "application/x-ipynb+json",
    "rtf": "application/rtf",
}

# Keep TeX engines inside the working directory and away from the shell
TEX_ENVIRONMENT = {"openin_any": "p", "openout_any": "p", "shell_escape": "f"}


def base_format(fmt: str) -> str:
    """The format name without extensions: 'markdown+smart-raw_tex' -> 'markdown'"""
    return re.split(r"[+-]", fmt, maxsplit=1)[0]


def extension_for(fmt: str) -> str:
    base = base_format(fmt)
    return EXTENSIONS.get(base, base)


def is_binary(fmt: str) -> bool:
    return base_format(fmt) in BINARY_FORMATS


TEXT_EXTENSIONS = (set(EXTENSIONS.values()) | {"rst", "org", "txt", "textile"}) - {"zip"}


def mime_type_for(extension: str) -> str:
    return MIME_TYPES.get(extension, "text/plain" if extension in TEXT_EXTENSIONS
                          else "application/octet-stream")


class ConversionError(Exception):
    """Pandoc ran but failed; the message is its error output"""


@dataclass(frozen=True)
class PandocInfo:
    version: str
    input_formats: frozenset[str]
    output_formats: frozenset[str]


@dataclass(frozen=True)
class RunResult:
    stdout: bytes
    stderr: str


class PandocRunner:
    def __init__(self, pandoc_path: str = "pandoc", timeout_s: float = 120.0):
        self.pandoc_path = pandoc_path
        self.timeout_s = timeout_s
        self.info: PandocInfo | None = None

    def probe(self) -> PandocInfo:
        """Read pandoc's version and formats once at startup"""
        def lines(*args: str) -> list[str]:
            out = subprocess.run([self.pandoc_path, *args], capture_output=True, text=True,
                                 check=True, timeout=30).stdout
            return [line.strip() for line in out.splitlines() if line.strip()]

        try:
            version = lines("--version")[0].removeprefix("pandoc").strip()
        except FileNotFoundError as e:
            raise RuntimeError(f"pandoc executable not found: {self.pandoc_path}") from e
        self.info = PandocInfo(
            version=version,
            input_formats=frozenset(lines("--list-input-formats")),
            output_formats=frozenset(lines("--list-output-formats")) | {"pdf"},
        )
        return self.info

    def check_formats(self, input_format: str, output_format: str) -> None:
        if self.info is None:
            return
        if base_format(input_format) not in self.info.input_formats:
            raise ValueError(f"Pandoc cannot read '{input_format}'. Input formats: "
                             f"{', '.join(sorted(self.info.input_formats))}")
        if base_format(output_format) not in self.info.output_formats:
            raise ValueError(f"Pandoc cannot write '{output_format}'. Output formats: "
                             f"{', '.join(sorted(self.info.output_formats))}")

    async def run(self, args: list[str], cwd: Path, stdin: bytes | None = None,
                  env: dict[str, str] | None = None) -> RunResult:
        """Run pandoc with args in cwd; raise ConversionError or TimeoutError on failure"""
        logger.debug(f"Running pandoc {' '.join(args)}")
        process = await asyncio.create_subprocess_exec(
            self.pandoc_path, *args,
            cwd=cwd,
            env={**os.environ, **TEX_ENVIRONMENT, **(env or {})},
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin), self.timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError(f"Pandoc did not finish within {self.timeout_s:g} s") from None
        except asyncio.CancelledError:
            process.kill()
            raise

        errors = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise ConversionError(errors or f"pandoc exited with status {process.returncode}")
        return RunResult(stdout, errors)
