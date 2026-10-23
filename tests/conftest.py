import shutil
from pathlib import Path

import pytest

from pandoc_mcp.config import AppConfig, PandocConfig

ASSETS_DIR = Path(__file__).parent.parent / "assets"

requires_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not on PATH")
requires_xelatex = pytest.mark.skipif(shutil.which("xelatex") is None, reason="xelatex not on PATH")
requires_mermaid = pytest.mark.skipif(shutil.which("mmdc") is None, reason="mmdc not on PATH")


@pytest.fixture
def app_config(tmp_path, monkeypatch) -> AppConfig:
    for name in ("MCP_TRANSPORT", "MCP_AUTH_ENABLED", "PANDOC_MCP_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    config = AppConfig(pandoc=PandocConfig(
        public_base_url="http://pandoc.test:8301",
        output_dir=tmp_path / "documents",
        assets_dir=ASSETS_DIR,
        timeout_s=60,
    ))
    config.server.transport = "stdio"
    return config
