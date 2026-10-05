#!/usr/bin/env python3
"""Read-only drift check for a two-folder workspace.

Usage:
    python3 audit.py /path/to/workspace [--json] [--stale-days N]

Exit code: 0 = no problems, 1 = at least one problem (warnings/info alone exit 0).
It never changes, moves or deletes anything.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DATE_RE,
    DELIVERABLE_EXTS,
    MEMORY_FILE_NAMES,
    SLUG_RE,
    TYPE_FOLDERS,
    is_hidden,
    is_junk,
    load_config,
    parse_registry,
    type_for_ext,
    type_folders,
)


@dataclass
class Finding:
    severity: str  # problem | warning | info
    code: str
    path: str
    message: str
    fix: str


def _visible(p: Path) -> bool:
    return not is_hidden(p.name)


def _in_hidden(path: Path, base: Path) -> bool:
    return any(is_hidden(part) for part in path.relative_to(base).parts)


def audit(root: Path, stale_days: int | None = None) -> list[Finding]:
    cfg = load_config(root)
    stale_days = stale_days if stale_days is not None else int(cfg.get("stale_days", 60))
    undated_ok = cfg.get("undated_ok", [])
    tfolders = type_folders(cfg)
    findings: list[Finding] = []
    if cfg.get("_config_error"):
        findings.append(Finding("warning", "CONFIG_INVALID", ".two-folder.json", cfg["_config_error"],
                                "Fix the file (it must be valid JSON). Defaults are being used meanwhile."))

    def add(sev: str, code: str, path: Path | str, msg: str, fix: str) -> None:
        rel = path.relative_to(root).as_posix() if isinstance(path, Path) else str(path)
        findings.append(Finding(sev, code, rel or ".", msg, fix))

    # 1. Root items -----------------------------------------------------
    allow = set(cfg["root_allow"])
    for item in sorted(root.iterdir()):
        if item.name in allow or (is_hidden(item.name) and not (item.is_file() and is_junk(item.name))):
            continue
        if item.is_file() and is_junk(item.name):
            add("info", "JUNK", item, "Junk file at the top of the workspace.", "Never delete it yourself: move it to _review/ for the user to decide (cleanup.py does this).")
        else:
            add(
                "problem",
                "ROOT_EXTRA",
                item,
                "Not allowed at the top of the workspace.",
                "Move it into projects/<project>/<type>/, memory/ or general/. Leave any empty folder for the user to remove.",
            )

    projects_dir = root / "projects"
    memory_dir = root / "memory"
    registry_path = memory_dir / "reference" / "projects.md"
    rows = parse_registry(registry_path)

    if not registry_path.is_file():
        add("problem", "REGISTRY_MISSING", registry_path, "The project registry is missing.",
            "Run scaffold.py init on this folder; it creates the registry without touching existing files.")

    top_slugs = {r["slug"].split("/")[0] for r in rows}
    registered_folders = set(top_slugs)
    for r in rows:
        if r["projects_path"]:
            registered_folders.add(r["projects_path"].split("/")[0])
    registered_slugs = {r["slug"] for r in rows}

    # 2. Projects folders vs registry ------------------------------------
    project_dirs = [p for p in sorted(projects_dir.iterdir()) if p.is_dir() and _visible(p)] if projects_dir.is_dir() else []
    folder_names = {p.name for p in project_dirs}

    for p in project_dirs:
        if not SLUG_RE.match(p.name):
            add("warning", "BAD_FOLDER_NAME", p, "Folder name is not lowercase-kebab-case.",
                "Rename to lowercase words joined by hyphens, and use the same name in the registry.")
        if registry_path.is_file() and p.name not in registered_folders:
            add("problem", "UNREGISTERED_PROJECT", p, "This project folder has no row in the registry.",
                "Add a row to memory/reference/projects.md, or move/merge the folder into an existing project.")

    for r in rows:
        top = r["projects_path"].split("/")[0] if r["projects_path"] else r["slug"].split("/")[0]
        if top and projects_dir.is_dir() and top not in folder_names and r["status"].lower() not in {"planned", "memory-only"}:
            add("warning", "REGISTRY_ORPHAN", f"projects/{top}", f"Registry row '{r['slug']}' points to a folder that does not exist.",
                "Create the folder when the first file arrives, mark the row 'Planned', or remove the row.")

    if projects_dir.is_dir():
        for f in sorted(projects_dir.iterdir()):
            if f.is_file() and not is_hidden(f.name):
                if is_junk(f.name):
                    add("info", "JUNK", f, "Junk file.", "Never delete it yourself: move it to _review/ for the user to decide (cleanup.py does this).")
                else:
                    add("problem", "LOOSE_FILE", f, "Loose file directly in projects/.",
                        "Move it into projects/<project>/<type>/.")

    # 3. Near-duplicate names (singular/plural, underscores, spaces) -------
    def key(name: str) -> str:
        return re.sub(r"s$", "", name.lower().replace("_", "-").replace(" ", "-"))

    seen: dict[str, str] = {}
    for name in sorted(folder_names | top_slugs):
        k = key(name)
        if k in seen and seen[k] != name:
            add("warning", "NEAR_DUPLICATE", f"projects/{name}",
                f"'{name}' and '{seen[k]}' look like the same project spelled two ways.",
                "Pick one spelling, merge the folders, and fix the registry.")
        seen.setdefault(k, name)

    # 4. Inside each project (and general/) --------------------------------
    def check_type_tree(base: Path, label: str, slug_path: str | None) -> None:
        for entry in sorted(base.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_file():
                if is_junk(entry.name):
                    add("info", "JUNK", entry, "Junk file.", "Never delete it yourself: move it to _review/ for the user to decide (cleanup.py does this).")
                else:
                    add("problem", "LOOSE_FILE", entry, f"Loose file at the top of {label}.",
                        f"Move it into a type folder ({', '.join(sorted(tfolders))}).")
                continue
            if entry.name in tfolders:
                check_type_folder(entry)
            else:
                child_slug = f"{slug_path}/{entry.name}" if slug_path else None
                if child_slug and child_slug in registered_slugs:
                    check_type_tree(entry, f"{label}/{entry.name}", child_slug)
                else:
                    add("warning", "UNKNOWN_FOLDER", entry,
                        "Not a standard type folder or a registered sub-project.",
                        "Rename it to a type folder, or register it as a sub-project in the registry.")

    def check_type_folder(folder: Path) -> None:
        for f in sorted(folder.rglob("*")):
            if not f.is_file() or _in_hidden(f, folder):
                continue
            rel = f.relative_to(root).as_posix()
            if is_junk(f.name):
                add("info", "JUNK", f, "Junk file.", "Never delete it yourself: move it to _review/ for the user to decide (cleanup.py does this).")
                continue
            # A repo or project kept inside code/ (code/20261004-my-repo/...) keeps its own
            # file names and may carry its own memory files: only its top level is checked.
            if folder.name == "code" and len(f.relative_to(folder).parts) > 1:
                continue
            if f.name in MEMORY_FILE_NAMES:
                add("warning", "MEMORY_FILE_IN_HUMAN_TREE", f,
                    "AI memory files belong under memory/, not with your finished files.",
                    "Move it into the matching memory/<category>/<project>/ folder.")
            # Files inside a dated bundle folder (documents/20261004-brand-kit/logo.png) keep their names.
            bundled = any(DATE_RE.match(part) for part in f.relative_to(folder).parts[:-1])
            if not bundled and not DATE_RE.match(f.name) and not any(fnmatch.fnmatch(rel, g) for g in undated_ok):
                add("warning", "NO_DATE_PREFIX", f, "File name does not start with YYYYMMDD-.",
                    "Rename to YYYYMMDD-name.ext, or list it under undated_ok in .two-folder.json if it is a living template.")
            expected = type_for_ext(f.suffix)
            if folder.name in TYPE_FOLDERS and folder.name != "exports" and expected and expected != folder.name:
                add("info", "WRONG_TYPE_FOLDER", f, f"A {f.suffix} file usually goes in {expected}/.",
                    f"Move it to {expected}/ if that makes it easier to find.")

    for p in project_dirs:
        check_type_tree(p, f"projects/{p.name}", p.name)
    general = root / "general"
    if general.is_dir():
        check_type_tree(general, "general", None)

    # 5. Memory side ---------------------------------------------------------
    if memory_dir.is_dir():
        for f in sorted(memory_dir.rglob("*")):
            if not f.is_file() or _in_hidden(f, memory_dir):
                continue
            if f.suffix.lower() in DELIVERABLE_EXTS:
                add("problem", "DELIVERABLE_IN_MEMORY", f,
                    "A finished human file is sitting in the AI's memory folder.",
                    "Move it to projects/<project>/<type>/ with a YYYYMMDD- name.")
            if f.name == "context.md":
                try:
                    st = f.stat()
                except OSError:
                    continue
                age_days = (time.time() - st.st_mtime) / 86400
                if st.st_size > 50_000:
                    add("warning", "OVERSIZED_CONTEXT", f, f"context.md is {st.st_size // 1000} KB (over 50 KB).",
                        "Split detail into a second file and keep context.md short.")
                if age_days > stale_days:
                    add("info", "STALE_CONTEXT", f, f"context.md not updated for {int(age_days)} days.",
                        "Check whether it is still true; update or mark the project Archived.")

    for r in rows:
        if not r["memory_path"]:
            continue
        mdir = memory_dir / r["memory_path"]
        if not mdir.is_dir():
            add("warning", "MEMORY_FOLDER_MISSING", f"memory/{r['memory_path']}",
                f"Registry row '{r['slug']}' has no memory folder.", "Create it with context.md and log.md.")
            continue
        for needed in ("context.md", "log.md"):
            if not (mdir / needed).is_file():
                add("warning", "MEMORY_FILE_MISSING", mdir / needed, f"Missing {needed}.", "Create it from the template.")

    return findings


ORDER = {"problem": 0, "warning": 1, "info": 2}
LABEL = {"problem": "PROBLEM", "warning": "WARNING", "info": "INFO"}


def render(findings: list[Finding], root: Path) -> str:
    if not findings:
        return f"Workspace looks clean: {root}\n"
    counts = {s: sum(1 for f in findings if f.severity == s) for s in ORDER}
    out = [f"Workspace check: {root}",
           f"{counts['problem']} problem(s), {counts['warning']} warning(s), {counts['info']} note(s)", ""]
    for sev in ("problem", "warning", "info"):
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        out.append(f"== {LABEL[sev]} ==")
        for f in sorted(group, key=lambda x: (x.code, x.path)):
            out.append(f"[{f.code}] {f.path}")
            out.append(f"    {f.message}")
            out.append(f"    Fix: {f.fix}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only drift check for a two-folder workspace.")
    ap.add_argument("workspace", help="path to the workspace root")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    ap.add_argument("--stale-days", type=int, default=None)
    args = ap.parse_args()
    root = Path(args.workspace).expanduser().resolve()
    if not root.is_dir():
        print(f"Not a folder: {root}", file=sys.stderr)
        return 2
    findings = audit(root, args.stale_days)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        print(render(findings, root))
    return 1 if any(f.severity == "problem" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
