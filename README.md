# pandoc-mcp

An MCP server that converts documents between formats with [Pandoc](https://pandoc.org):
markdown, HTML, DOCX, PDF (XeLaTeX), ODT, EPUB, PPTX, LaTeX, reStructuredText, Typst, AsciiDoc,
Jupyter notebooks and everything else your pandoc reads or writes.

It is built on [mcp-service-kit](https://github.com/geosp/mcp-service-kit) and runs over stdio
(local) or HTTP with a bearer token (Docker). It is a reimplementation of
[mcp-pandoc](https://github.com/lucasgaldinos/mcp-server-mcp-pandoc) with a few design changes:
see [Differences from mcp-pandoc](#differences-from-mcp-pandoc).

For how it is built (layers, conversion flow, storage, security model), see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tools

| Tool | What it does |
| --- | --- |
| `convert_document` | Convert one input to `output_format`. Input is exactly one of `contents` (text), `contents_base64` (a binary file), `document` (id of a stored document) or `input_path` (local stdio only). Options: `input_format`, `filename`, `standalone`, `table_of_contents`, `metadata`, and the named assets `defaults`, `reference_doc`, `template`, `filters`. |
| `get_document` | Fetch a stored document (the latest if no id) with its metadata and content. |
| `list_assets` | List the server's named filters, defaults files, reference docs and templates, plus the pandoc version. |

Each result is stored on the server and returned with an **id** and a **download URL**:

- text output (markdown, HTML, LaTeX, ...) also comes back inline;
- binary output (DOCX, PDF, ODT, EPUB, PPTX) comes back as an embedded base64 resource, up to
  `PANDOC_MCP_MAX_INLINE_BYTES`;
- `inline: false` returns only the id and URL, which keeps large files out of the model's context.

The id can be passed as `document` to chain conversions, e.g. DOCX → markdown, edit, markdown → DOCX, without resending the bytes.

## Mermaid diagrams

Mermaid diagrams written as fenced code blocks are rendered automatically, wherever
[mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (`mmdc`) is installed. The Docker
image includes it, with Debian's Chromium. The format depends on the output:

| Output | Diagram becomes |
| --- | --- |
| html, revealjs and other HTML slides | inline SVG |
| pdf, latex, beamer | vector PDF figure |
| docx, odt, pptx, epub, typst, ... | 2x PNG, embedded in the file |
| markdown, gfm, commonmark, ipynb | unchanged code block (GitHub etc. render it) |

````markdown
``` {.mermaid caption="Request flow" width="80%"}
flowchart LR
  User --> API --> DB[(Database)]
```
````

- `caption` turns the diagram into a numbered figure, and `width` sizes it.
- A diagram with a syntax error stays as code, and mermaid's error appears in the result's
  `warnings`, so the model can fix it and convert again.
- Pass `render_mermaid: false` to keep the code blocks.
- Without `mmdc`, e.g. a local install without Node, diagrams stay as code, and `list_assets`
  reports mermaid as unavailable.

## Running

### Local (stdio)

You need pandoc, plus `xelatex` for PDF output (`sudo apt install pandoc texlive-xetex
texlive-latex-extra`, or the [pandoc release](https://github.com/jgm/pandoc/releases)).

```bash
uv sync
uv run pandoc-mcp --mode stdio
# Claude Code
claude mcp add pandoc -- uv --directory /path/to/pandoc-mcp run pandoc-mcp
```

In stdio mode, `input_path` and `output_path` are accepted, so a conversion can read and write
files next to you directly.

### Docker (HTTP)

The image includes pandoc and TeX Live (XeLaTeX).

```bash
cp .env.example .env            # set PANDOC_MCP_TOKEN (openssl rand -hex 32)
docker compose up -d --build
claude mcp add --transport http pandoc http://localhost:8301/mcp \
  --header "Authorization: Bearer $PANDOC_MCP_TOKEN"
```

The server also provides these routes:

- `GET /documents/<id>/<filename>`: download a stored document. The id is a random uuid4.
- `POST /upload` (multipart field `file`, bearer token required) returns `{"id", "url", ...}`. Use it
  for files the model cannot see: `curl -H "Authorization: Bearer $PANDOC_MCP_TOKEN" -F file=@report.docx http://localhost:8301/upload`.
- `GET /upload`: a browser form for the same.

## Assets

Filters, defaults files, reference documents and templates are installed by the administrator
in `PANDOC_MCP_ASSETS_DIR`. Tools refer to them **by name**:

```
assets/
  defaults/        *.yaml   pandoc defaults files      e.g. academic-pdf.yaml, report-html.yaml
  filters/         *.lua    Lua filters (or executable JSON filters)   e.g. pagebreak.lua
  reference-docs/  *.docx|odt|pptx   styling for Office output         e.g. default.docx
  templates/                pandoc templates
```

If a text asset's first line is a comment (`#`, `--` or `%`), that line becomes its description in `list_assets`.

- A reference doc is a copy of pandoc's default
  (`pandoc -o custom.docx --print-default-data-file reference.docx`) with the styles edited in
  Word or LibreOffice.
- JSON filters must already be executable. The server never runs chmod on them.
- In Docker, `./assets` is mounted read-only, so you can add assets without rebuilding the image.

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `MCP_AUTH_ENABLED` | stdio, 0.0.0.0, 3000 (8301 in Docker), true | from mcp-service-kit; CLI flags `--mode --host --port --no-auth` |
| `PANDOC_MCP_TOKEN` | | Bearer token; required for HTTP unless `--no-auth` |
| `PANDOC_MCP_PUBLIC_BASE_URL` | `http://localhost:8301` | Base of download URLs |
| `PANDOC_MCP_OUTPUT_DIR` | `data/documents` | Document store |
| `PANDOC_MCP_ASSETS_DIR` | `assets` | Named assets |
| `PANDOC_MCP_PANDOC_PATH` | `pandoc` | Pandoc executable |
| `PANDOC_MCP_PDF_ENGINE` | `xelatex` | `--pdf-engine` for PDF output |
| `PANDOC_MCP_TIMEOUT_S` | `120` | Pandoc is killed after this |
| `PANDOC_MCP_MAX_INPUT_BYTES` | 20 MB | Inline, base64-decoded or uploaded input |
| `PANDOC_MCP_MAX_INLINE_CHARS` | 50000 | Longest text returned inline |
| `PANDOC_MCP_MAX_INLINE_BYTES` | 5 MB | Largest binary returned as embedded base64 |
| `PANDOC_MCP_RETENTION_DAYS` | 7 | Stored documents are purged after this |
| `PANDOC_MCP_MERMAID` | `true` | Render mermaid code blocks when `mmdc` is available |
| `PANDOC_MCP_MMDC_PATH` | `mmdc` | mermaid-cli executable |
| `PANDOC_MCP_MERMAID_THEME` | `default` | `default`, `neutral`, `dark` or `forest` |
| `PANDOC_MCP_PUPPETEER_CONFIG` | (`/etc/puppeteer.json` in Docker) | Chromium settings for mmdc |
| `PANDOC_MCP_ALLOW_LOCAL_PATHS` | stdio only | Accept `input_path` / `output_path` |

## Security model

These protections apply over HTTP:

- **No client-supplied paths.**
  - `input_path` and `output_path` are disabled.
  - Assets are resolved by bare name inside the assets directory.
  - Upload filenames are reduced to a plain name.
- **Pandoc runs with `--sandbox`.** Readers and writers cannot read local files. For example,
  `![x](/etc/passwd)` → DOCX would otherwise embed the file. Each conversion also runs in its
  own temporary directory.
- **TeX is confined.** It runs with `openin_any=p openout_any=p shell_escape=f`, so raw LaTeX
  such as `\input{/etc/passwd}` is refused by xelatex.
- **Arguments go straight to pandoc.** It is run as a subprocess with an argument list, never a
  shell, and is killed after `PANDOC_MCP_TIMEOUT_S`.
- **Defaults files can't change the target.** `--defaults` is always placed first, so a defaults
  file cannot override the requested formats or the output file.
- **Mermaid renders in Chromium.** Untrusted diagrams are rendered in headless Chromium,
  inside the container, with mermaid's default `strict` security level (no scripts or click
  handlers). Chromium's own sandbox is off, because it needs privileges the container does not
  have. Set `PANDOC_MCP_MERMAID=false` if that is not acceptable.
- **Filters are trusted code.** They are whatever the administrator installed, so only install
  filters you trust.

## Differences from mcp-pandoc

- **stdio protocol fixed.** mcp-pandoc's `print()` calls wrote to stdout, which corrupts the
  stdio protocol. Logging here goes to stderr.
- **Non-blocking with a timeout.** Pandoc runs as an async subprocess with a timeout instead of a
  blocking `pypandoc` call inside the event loop.
- **Formats come from pandoc itself.** They are validated against `pandoc --list-input-formats`
  and `--list-output-formats`, not a fixed list of 10. That list offered PDF as an input and
  used `txt` where pandoc calls it `plain`.
- **No server paths needed.** Binary formats come back inline as base64 or by URL. mcp-pandoc
  required a path on the server's filesystem.
- **Base64 input.** Binary input can be sent as base64, so a model can convert files it holds or
  produced.
- **Named assets.** Filters and defaults files are named, admin-installed assets instead of
  arbitrary paths, and filters are never chmod'ed.
- **Code structure.** The code uses mcp-service-kit's layout (`features/<tool>/tool.py`, pydantic
  config, standard CLI), and tests run through a real MCP client.

## Development

```bash
uv sync
uv run pytest     # tests needing pandoc / xelatex are skipped when those are missing
uv run ruff check
```

To run the whole suite, PDF included, inside the image:

```bash
docker build -t pandoc-mcp . && docker run --rm --user 0 -v $PWD/tests:/app/tests:ro \
  pandoc-mcp sh -c 'uv sync --frozen -q && uv run --frozen pytest -q -p no:cacheprovider'
```
