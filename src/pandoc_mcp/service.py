"""
MCP service for pandoc-mcp

Builds the pandoc runner, document store and asset library, registers the
tools from features/*/tool.py, serves stored documents at
/documents/<id>/<filename> and accepts uploads at /upload (POST, or GET for a
browser form).
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
from .auth import bearer_token, token_matches
from .config import AppConfig
from .documents import DocumentStore
from .pandoc_service import PandocService
from .runner import PandocRunner

logger = logging.getLogger(__name__)


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
        max_bytes = self.config.pandoc.max_input_bytes
        upload_page = (Path(__file__).parent / "upload_page.html").read_text()

        @mcp.custom_route("/documents/{doc_id}/{filename}", methods=["GET"],
                          include_in_schema=False)
        async def get_document(request: Request) -> Response:
            path = pandoc.store.path_for(request.path_params["doc_id"],
                                         request.path_params["filename"])
            if path is None:
                return PlainTextResponse("Not found", status_code=404)
            document = pandoc.store.get(request.path_params["doc_id"])
            return FileResponse(path, media_type=document.mime_type, filename=path.name)

        @mcp.custom_route("/upload", methods=["GET"], include_in_schema=False)
        async def upload_form(request: Request) -> Response:
            return HTMLResponse(upload_page)

        @mcp.custom_route("/upload", methods=["POST"], include_in_schema=False)
        async def upload(request: Request) -> Response:
            # FastMCP's auth covers /mcp only, so custom routes check the token themselves
            if not self._authorized(request):
                return JSONResponse({"error": "missing or invalid bearer token"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
            too_large = JSONResponse({"error": f"file is larger than {max_bytes} bytes"},
                                     status_code=413)
            if int(request.headers.get("content-length") or 0) > max_bytes + 64 * 1024:
                return too_large
            async with request.form(max_files=1, max_fields=0) as form:
                file = form.get("file")
                if file is None or isinstance(file, str):
                    return JSONResponse({"error": "send the document as multipart field 'file'"},
                                        status_code=400)
                if file.size is not None and file.size > max_bytes:
                    return too_large
                data = await file.read()
            if not data:
                return JSONResponse({"error": "the file is empty"}, status_code=400)
            stored = pandoc.store_upload(data, file.filename or "upload")
            logger.info(f"Stored upload {stored.id} ({stored.filename}, {stored.size} bytes)")
            return JSONResponse({"id": stored.id, "url": stored.url,
                                 "filename": stored.filename, "size": stored.size})

    def _authorized(self, request: Request) -> bool:
        server, token = self.config.server, self.config.pandoc.token
        if not (server.transport == "http" and server.auth_enabled):
            return True
        given = bearer_token(request.headers.get("authorization"))
        return token is not None and given is not None and token_matches(token, given)
