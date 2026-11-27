"""Serve the built React site from the same server as the API (one container, one URL)."""
from pathlib import Path


def resolve_static(root: Path, url_path: str) -> Path | None:
    """The real file if it exists inside `root`, otherwise index.html (the React app decides what to show, e.g. /agent).
    Returns None for API / WebSocket paths so a wrong API URL stays a clean 404 instead of returning the website."""
    clean = url_path.lstrip("/")
    if clean in ("api", "ws") or clean.startswith(("api/", "ws/")):
        return None
    root = root.resolve()
    if clean:
        candidate = (root / clean).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):   # blocks ../ tricks
            return candidate
    index = root / "index.html"
    return index if index.is_file() else None
