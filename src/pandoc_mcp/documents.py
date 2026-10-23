"""
Storage for converted and uploaded documents

Each document lives in its own directory, <output_dir>/<id>/, holding the file
under its original name plus meta.json describing how it was produced. <id> is
a random uuid4 hex string and doubles as the unguessable part of its URL,
<public_base_url>/documents/<id>/<filename>.
"""

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .runner import mime_type_for

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
METADATA_FILE = "meta.json"


@dataclass(frozen=True)
class StoredDocument:
    id: str
    path: Path
    url: str
    filename: str
    size: int

    @property
    def extension(self) -> str:
        return self.path.suffix.removeprefix(".").lower()

    @property
    def mime_type(self) -> str:
        return mime_type_for(self.extension)


def safe_filename(name: str, default: str = "document") -> str:
    """A plain file name: no directories, no unusual characters"""
    name = UNSAFE_CHARS.sub("_", Path(name.replace("\\", "/")).name).strip(" .")
    return name[:120] or default


class DocumentStore:
    def __init__(self, directory: Path, public_base_url: str):
        self.directory = directory
        self.public_base_url = public_base_url.rstrip("/")

    def ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, filename: str, metadata: dict[str, Any] | None = None
             ) -> StoredDocument:
        doc_id = uuid.uuid4().hex
        folder = self.directory / doc_id
        folder.mkdir(parents=True)
        path = folder / safe_filename(filename)
        path.write_bytes(data)
        meta = {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **(metadata or {})}
        (folder / METADATA_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        return self._document(doc_id, path)

    def get(self, doc_id: str) -> StoredDocument | None:
        folder = self._folder(doc_id)
        if folder is None:
            return None
        files = [p for p in folder.iterdir() if p.is_file() and p.name != METADATA_FILE]
        return self._document(doc_id, files[0]) if files else None

    def path_for(self, doc_id: str, filename: str) -> Path | None:
        """The stored file for a download URL, or None"""
        document = self.get(doc_id)
        return document.path if document and document.filename == filename else None

    def latest(self) -> StoredDocument | None:
        folders = [p for p in self.directory.glob("*") if ID_PATTERN.match(p.name)]
        for folder in sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True):
            document = self.get(folder.name)
            if document:
                return document
        return None

    def metadata(self, document: StoredDocument) -> dict[str, Any]:
        try:
            return json.loads((document.path.parent / METADATA_FILE).read_text())
        except (OSError, ValueError):
            return {}

    def purge(self, older_than_days: float) -> int:
        """Delete documents older than the retention period; return how many"""
        if not self.directory.is_dir():
            return 0
        cutoff = time.time() - older_than_days * 86400
        removed = 0
        for folder in self.directory.glob("*"):
            if ID_PATTERN.match(folder.name) and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed

    def _folder(self, doc_id: str) -> Path | None:
        if not ID_PATTERN.match(doc_id):
            return None
        folder = self.directory / doc_id
        return folder if folder.is_dir() else None

    def _document(self, doc_id: str, path: Path) -> StoredDocument:
        url = f"{self.public_base_url}/documents/{doc_id}/{quote(path.name)}"
        return StoredDocument(doc_id, path, url, path.name, path.stat().st_size)
