"""
Named conversion assets installed by the server's administrator

The assets directory has one subdirectory per kind:

    filters/         Lua filters (*.lua) or executable JSON filters
    defaults/        pandoc defaults files (*.yaml)
    reference-docs/  styling references for docx, odt and pptx output
    templates/       pandoc templates

Tools refer to assets by file name only ("academic-pdf.yaml" or just
"academic-pdf"), never by path, so a client cannot point pandoc at an
arbitrary file or run an arbitrary program as a filter. A first line starting
with "#" or "--" in a text asset is shown as its description.
"""

import os
from dataclasses import dataclass
from pathlib import Path

KINDS = ("filters", "defaults", "reference-docs", "templates")


@dataclass(frozen=True)
class Asset:
    kind: str
    name: str
    path: Path
    description: str | None


class AssetLibrary:
    def __init__(self, directory: Path):
        self.directory = directory

    def resolve(self, kind: str, name: str) -> Asset:
        """Find an asset by name, with or without its file extension"""
        if kind not in KINDS:
            raise ValueError(f"Unknown asset kind: {kind}")
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"Invalid {kind} name '{name}': give a name from list_assets, "
                             "not a path")
        for asset in self.of_kind(kind):
            if name in (asset.name, asset.path.stem):
                return asset
        available = ", ".join(a.name for a in self.of_kind(kind)) or "none installed"
        raise ValueError(f"No {kind} asset named '{name}' (available: {available})")

    def of_kind(self, kind: str) -> list[Asset]:
        folder = self.directory / kind
        if not folder.is_dir():
            return []
        assets = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if kind == "filters" and path.suffix != ".lua" and not os.access(path, os.X_OK):
                continue  # a JSON filter must already be executable; never chmod it here
            assets.append(Asset(kind, path.name, path, _description(path)))
        return assets

    def all(self) -> dict[str, list[Asset]]:
        return {kind: self.of_kind(kind) for kind in KINDS}


def _description(path: Path) -> str | None:
    if path.suffix in (".docx", ".odt", ".pptx"):
        return None
    try:
        with path.open(encoding="utf-8") as f:
            first = f.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None
    for marker in ("#!", "--", "#", "%"):
        if first.startswith(marker):
            return None if marker == "#!" else first.removeprefix(marker).strip() or None
    return None
