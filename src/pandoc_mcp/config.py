"""
Configuration for pandoc-mcp

Two sections, each read from its own environment variables:
- server: mcp-service-kit's BaseServerConfig (MCP_TRANSPORT, MCP_HOST, MCP_PORT, ...)
- pandoc: PandocConfig (PANDOC_MCP_*)

The prefix is PANDOC_MCP_ rather than PANDOC_ so it never collides with
variables pandoc itself or its Lua filters look at.
"""

from pathlib import Path

from mcp_service_kit import BaseServerConfig
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PandocConfig(BaseSettings):
    """
    Pandoc binary, auth, storage and request limits

    Environment variables: PANDOC_MCP_TOKEN, PANDOC_MCP_PUBLIC_BASE_URL,
    PANDOC_MCP_OUTPUT_DIR, PANDOC_MCP_ASSETS_DIR, PANDOC_MCP_PDF_ENGINE, ...
    """
    model_config = SettingsConfigDict(env_prefix="PANDOC_MCP_", extra="ignore")

    token: SecretStr | None = Field(
        default=None,
        description="Bearer token clients must send over HTTP transport"
    )
    public_base_url: str = Field(
        default="http://localhost:8301",
        description="Base URL clients use to download stored documents"
    )
    output_dir: Path = Field(
        default=Path("data/documents"),
        description="Where converted and uploaded documents are stored"
    )
    assets_dir: Path = Field(
        default=Path("assets"),
        description="Directory with filters/, defaults/, reference-docs/ and templates/"
    )
    pandoc_path: str = Field(default="pandoc", description="Pandoc executable")
    pdf_engine: str = Field(default="xelatex", description="Engine pandoc uses for PDF output")
    timeout_s: float = Field(default=120.0, gt=0, description="Longest one conversion may run")
    max_input_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1024,
        description="Largest input accepted (inline, base64-decoded or uploaded)"
    )
    max_inline_chars: int = Field(
        default=50_000,
        ge=0,
        description="Longest text output returned inline in the tool result"
    )
    max_inline_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=0,
        description="Largest binary output returned inline as an embedded base64 resource"
    )
    retention_days: float = Field(
        default=7.0,
        gt=0,
        description="Stored documents older than this are deleted"
    )
    upload_link_ttl_s: float = Field(
        default=600.0,
        gt=0,
        description="How long an upload link from create_upload_link stays usable"
    )
    upload_link_max_files: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Most files one upload link may accept"
    )
    upload_link_max_active: int = Field(
        default=100,
        ge=1,
        description="Most upload links open at the same time"
    )
    mermaid: bool = Field(
        default=True,
        description="Render ```mermaid code blocks as diagrams (needs mermaid-cli)"
    )
    mmdc_path: str = Field(default="mmdc", description="mermaid-cli executable")
    mermaid_theme: str = Field(
        default="default",
        description="Mermaid theme: default, neutral, dark or forest"
    )
    puppeteer_config: Path | None = Field(
        default=None,
        description="Puppeteer JSON config for mmdc's headless Chromium (e.g. --no-sandbox)"
    )
    allow_local_paths: bool | None = Field(
        default=None,
        description="Accept input_path/output_path on the server's filesystem "
                    "(default: only with stdio transport)"
    )


class AppConfig(BaseModel):
    """
    Complete application configuration

    The server section goes to the server; the whole object goes to the service.
    """
    server: BaseServerConfig = Field(default_factory=BaseServerConfig)
    pandoc: PandocConfig = Field(default_factory=PandocConfig)

    @property
    def local_paths_allowed(self) -> bool:
        if self.pandoc.allow_local_paths is not None:
            return self.pandoc.allow_local_paths
        return self.server.transport == "stdio"
