"""
MCP service for pandoc-mcp

Builds the pandoc runner, document store and asset library, registers the
tools from features/*/tool.py, serves stored documents at
/documents/<id>/<filename> and accepts uploads through short-lived links at
/upload/<token> (POST, or GET for a browser form).
"""

import importlib
import logging
import pkgutil
import shutil
from pathlib import Path

from fastmcp import FastMCP
from mcp_service_kit import BaseService
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)

from .assets import AssetLibrary
from .config import AppConfig
from .documents import DocumentStore
from .pandoc_service import PandocService
from .runner import PandocRunner
from .uploads import UploadLinks

logger = logging.getLogger(__name__)

LINK_GONE_PAGE = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<title>Upload link expired</title><body style='font:15px system-ui;max-width:640px;"
    "margin:2rem auto;padding:0 16px'><h1>This upload link is invalid or expired</h1>"
    "<p>Links work for a few minutes and a limited number of files. "
    "Ask the assistant for a new one.</p>"
)


class PandocMCPService(BaseService):
    """
    MCP service wrapper for pandoc conversions

    Each package in features/ with a tool.py exposing register_tool(mcp, pandoc)
    contributes one tool.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.pandoc: PandocService | None = None

    def initialize(self) -> None:
        pandoc_config = self.config.pandoc
        runner = PandocRunner(pandoc_config.pandoc_path, pandoc_config.timeout_s)
        info = runner.probe()
        store = DocumentStore(pandoc_config.output_dir, pandoc_config.public_base_url)
        store.ensure_directory()
        local_paths = self.config.local_paths_allowed
        self.pandoc = PandocService(
            pandoc_config, runner, store, AssetLibrary(pandoc_config.assets_dir),
            local_paths=local_paths, sandbox=not local_paths,
        )
        self.pandoc.mermaid_env = self._mermaid_env()
        if self.config.server.transport == "http":  # links need the HTTP routes
            self.pandoc.uploads = UploadLinks(pandoc_config.upload_link_ttl_s,
                                              pandoc_config.upload_link_max_active)
        logger.info(f"Using pandoc {info.version}; local paths "
                    f"{'allowed' if local_paths else 'disabled'}; mermaid "
                    f"{'enabled' if self.pandoc.mermaid_env else 'disabled'}")

    def _mermaid_env(self) -> dict[str, str] | None:
        config = self.config.pandoc
        if not config.mermaid:
            return None
        mmdc = shutil.which(config.mmdc_path)
        if mmdc is None:
            logger.warning(f"mermaid-cli ({config.mmdc_path}) not found: mermaid blocks stay code")
            return None
        env = {"PANDOC_MCP_MMDC": mmdc, "PANDOC_MCP_MERMAID_THEME": config.mermaid_theme}
        if config.puppeteer_config:
            env["PANDOC_MCP_PUPPETEER_CONFIG"] = str(config.puppeteer_config.resolve())
        return env

    def get_service_name(self) -> str:
        return "pandoc"

    def register_mcp_tools(self, mcp: FastMCP) -> None:
        features_package = f"{__package__}.features"
        features_path = Path(__file__).parent / "features"

        for _, feature_name, is_pkg in pkgutil.iter_modules([str(features_path)]):
            if not is_pkg:
                continue
            tool_module = importlib.import_module(f"{features_package}.{feature_name}.tool")
            tool_module.register_tool(mcp, self.pandoc)
            logger.info(f"Registered tool from feature: {feature_name}")

        pandoc = self.pandoc

        @mcp.custom_route("/documents/{doc_id}/{filename}", methods=["GET"],
                          include_in_schema=False)
        async def get_document(request: Request) -> Response:
            path = pandoc.store.path_for(request.path_params["doc_id"],
                                         request.path_params["filename"])
            if path is None:
                return PlainTextResponse("Not found", status_code=404)
            document = pandoc.store.get(request.path_params["doc_id"])
            return FileResponse(path, media_type=document.mime_type, filename=path.name)

        if pandoc.uploads is not None:
            self._register_upload_routes(mcp)

    def _register_upload_routes(self, mcp: FastMCP) -> None:
        pandoc = self.pandoc
        max_bytes = self.config.pandoc.max_input_bytes
        upload_page = (Path(__file__).parent / "upload_page.html").read_text()

        @mcp.custom_route("/upload/{token}", methods=["GET"], include_in_schema=False)
        async def upload_form(request: Request) -> Response:
            if pandoc.uploads.for_token(request.path_params["token"]) is None:
                return HTMLResponse(LINK_GONE_PAGE, status_code=404)
            return HTMLResponse(upload_page)

        @mcp.custom_route("/upload/{token}", methods=["POST"], include_in_schema=False)
        async def upload(request: Request) -> Response:
            # The link itself is the credential: no Authorization header
            grant = pandoc.uploads.for_token(request.path_params["token"])
            if grant is None:
                return JSONResponse({"error": "this upload link is invalid, expired or used up"},
                                    status_code=404)
            too_large = JSONResponse({"error": f"each file must be at most {max_bytes} bytes"},
                                     status_code=413)
            if int(request.headers.get("content-length") or 0) > \
                    grant.remaining * max_bytes + 64 * 1024:
                return too_large
            files: list[tuple[str, bytes]] = []
            async with request.form(max_files=grant.remaining, max_fields=0) as form:
                for file in form.getlist("file"):
                    if isinstance(file, str):
                        continue
                    if file.size is not None and file.size > max_bytes:
                        return too_large
                    files.append((file.filename or "upload", await file.read()))
            if not files:
                return JSONResponse({"error": "send the files as multipart field 'file'"},
                                    status_code=400)
            if any(not data for _, data in files):
                return JSONResponse({"error": "empty files are not accepted"}, status_code=400)
            if len(files) > grant.remaining:
                return JSONResponse({"error": f"this link accepts {grant.remaining} more file(s)"},
                                    status_code=400)
            stored = []
            for filename, data in files:
                document = pandoc.store_upload(data, filename, grant.upload_id)
                pandoc.uploads.record(grant, document.id)
                stored.append({"filename": document.filename, "size": document.size})
                logger.info(f"Stored upload {document.id} ({document.filename}, "
                            f"{document.size} bytes) for {grant.upload_id}")
            return JSONResponse({"files": stored, "remaining": grant.remaining})
