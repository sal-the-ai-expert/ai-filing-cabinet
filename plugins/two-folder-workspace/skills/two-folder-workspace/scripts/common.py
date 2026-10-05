"""Shared helpers for the two-folder-workspace scripts. Standard library only."""
from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

CONFIG_NAME = ".two-folder.json"

# Holding area for anything the user must decide on (files queued for deletion,
# cleanup plans, move logs). Nothing in the workspace is ever deleted by these
# scripts; junk is moved here and listed in DELETE-QUEUE.md for the user.
REVIEW_DIR = "_review"
DELETE_QUEUE = "DELETE-QUEUE.md"

# Human-side type folders and the file extensions that belong in each.
TYPE_FOLDERS: dict[str, set[str]] = {
    "documents": {".docx", ".doc", ".md", ".txt", ".rtf", ".odt"},
    "spreadsheets": {".xlsx", ".xls", ".csv", ".ods"},
    "presentations": {".pptx", ".ppt", ".key", ".odp"},
    "pdfs": {".pdf"},
    "designs": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"},
    "code": {".py", ".js", ".ts", ".sh", ".rb", ".go"},
    "exports": set(),  # catch-all for anything else
}

# Files that are made for humans and must never live in the AI's memory/ tree.
DELIVERABLE_EXTS = {
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".key", ".pages", ".numbers",
}

# Files the AI's memory system uses; they do not belong in the human tree.
MEMORY_FILE_NAMES = {"context.md", "log.md", "_index.md"}

DEFAULT_CONFIG = {
    "version": 1,
    "root_allow": ["CLAUDE.md", "memory", "projects", "general", REVIEW_DIR, CONFIG_NAME],
    "enforce": ["root", "loose", "dateprefix", "memory-split", "no-delete"],
    "undated_ok": [],
    "extra_type_folders": [],
    "stale_days": 60,
}

JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_GLOBS = ["~$*", "*.tmp", ".~lock.*"]

DATE_RE = re.compile(r"^\d{8}-")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_hidden(name: str) -> bool:
    """Dotfiles and dot-folders (.git, .claude, .obsidian) are never treated as workspace content."""
    return name.startswith(".")


def is_junk(name: str) -> bool:
    return name in JUNK_NAMES or any(fnmatch.fnmatch(name, g) for g in JUNK_GLOBS)


def type_for_ext(ext: str) -> str | None:
    ext = ext.lower()
    for folder, exts in TYPE_FOLDERS.items():
        if ext in exts:
            return folder
    return None


def load_config(root: Path) -> dict:
    """Load .two-folder.json over the defaults. Bad values fall back to defaults.

    The reason for any fallback is stored under cfg["_config_error"] so the audit can report it.
    """
    cfg = dict(DEFAULT_CONFIG)
    path = root / CONFIG_NAME
    if not path.is_file():
        return cfg
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        cfg["_config_error"] = f"could not read {CONFIG_NAME}: {exc}"
        return cfg
    if not isinstance(loaded, dict):
        cfg["_config_error"] = f"{CONFIG_NAME} must contain a JSON object"
        return cfg
    for key, value in loaded.items():
        if key in ("root_allow", "enforce", "undated_ok", "extra_type_folders"):
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                cfg[key] = value
            else:
                cfg["_config_error"] = f"'{key}' must be a list of strings; using the default"
        elif key == "stale_days":
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                cfg[key] = value
            else:
                cfg["_config_error"] = "'stale_days' must be a positive whole number; using the default"
        else:
            cfg[key] = value
    return cfg


def find_root(start: Path) -> Path | None:
    """Walk up from `start` until a folder containing .two-folder.json is found."""
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    return None


def _clean_path(value: str) -> str:
    value = value.strip().strip("`").strip()
    for prefix in ("projects/", "memory/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip("/")


def parse_registry(path: Path) -> list[dict]:
    """Read the project registry (a markdown table) into a list of rows.

    Expected columns: Slug | Name | Memory path | Projects path | Status
    Rows whose first cell is not a kebab-case slug (headers, separators,
    prose tables) are ignored.
    """
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        slug = cells[0]
        if not re.match(r"^[a-z0-9][a-z0-9/_-]*$", slug):
            continue
        rows.append(
            {
                "slug": slug,
                "name": cells[1],
                "memory_path": _clean_path(cells[2]),
                "projects_path": _clean_path(cells[3]),
                "status": cells[4] if len(cells) > 4 else "",
            }
        )
    return rows


def type_folders(cfg: dict) -> set[str]:
    """Standard type folders plus any the user added in .two-folder.json (e.g. contracts, proposals)."""
    return set(TYPE_FOLDERS) | set(cfg.get("extra_type_folders", []))


# --- Remembered workspace ---------------------------------------------------
# The skill asks once which folder to keep everything in; init remembers it here
# so later sessions on the same computer can find it without asking again.

def pointer_file() -> Path:
    override = os.environ.get("TWO_FOLDER_POINTER_FILE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "two-folder-workspace" / "workspace.txt"


def remember_workspace(root: Path) -> bool:
    try:
        pf = pointer_file()
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(root) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def remembered_workspace() -> Path | None:
    try:
        text = pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    path = Path(text) if text else None
    return path if path and (path / CONFIG_NAME).is_file() else None
