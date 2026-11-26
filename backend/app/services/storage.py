"""Local file storage (development default).

All paths handed out are relative to the storage root and are resolved
defensively, so a crafted path can never escape the root directory.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.config import get_settings


def safe_name(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    return base[:120]


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("Invalid storage path")
        return p

    def write(self, rel: str, data: bytes) -> str:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return rel

    def read(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def exists(self, rel: str | None) -> bool:
        return bool(rel) and self.path(rel).exists()  # type: ignore[arg-type]

    def delete_dir(self, rel: str) -> None:
        p = self.path(rel)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().files_dir)
    return _storage
