#!/usr/bin/env python3
"""Create or extend a two-folder workspace. Never overwrites existing files.

Usage:
    python3 scaffold.py init  /path/to/workspace [--demo]
    python3 scaffold.py add-project /path/to/workspace SLUG --name "Display name" [--category work]
    python3 scaffold.py where          # print the workspace folder remembered on this computer
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CONFIG_NAME, DEFAULT_CONFIG, SLUG_RE, parse_registry, remember_workspace, remembered_workspace  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"

REGISTRY_HEADER = """---
name: projects
description: Registry of every project slug. Read this before creating any folder.
---
# Project registry

One canonical slug per project. A folder is only created AFTER its slug is listed here.
Sub-projects use `parent/child`.

| Slug | Name | Memory path | Projects path | Status |
|------|------|-------------|---------------|--------|
"""


def today() -> str:
    return dt.date.today().isoformat()


def stamp() -> str:
    return dt.date.today().strftime("%Y%m%d")


def render(template_name: str, **values: str) -> str:
    text = (TEMPLATES / template_name).read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def write_new(path: Path, content: str, created: list[str], root: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(path.relative_to(root).as_posix())


def init(root: Path, demo: bool) -> list[str]:
    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    write_new(root / CONFIG_NAME, json.dumps(DEFAULT_CONFIG, indent=2) + "\n", created, root)
    if (root / "CLAUDE.md").exists():
        print("Note: CLAUDE.md already exists and was left alone. Merge the rules from "
              "templates/CLAUDE.md.tmpl into it so your AI follows them.")
    write_new(root / "CLAUDE.md", render("CLAUDE.md.tmpl", date=today()), created, root)
    write_new(root / "memory" / "_index.md", render("index.md.tmpl", date=today()), created, root)
    write_new(root / "memory" / "glossary.md", render("glossary.md.tmpl"), created, root)
    write_new(root / "memory" / "reference" / "projects.md", REGISTRY_HEADER, created, root)
    write_new(root / "memory" / "reference" / "file-routing.md", render("file-routing.md.tmpl"), created, root)
    write_new(root / "memory" / "people" / "README.md", "# People\n\nOne short file per person: who they are, how they relate to your work. Keep it professional.\n", created, root)
    write_new(root / "memory" / "insights" / "lessons-learned.md", "# Lessons learned\n\nThings that went wrong once and should not happen again. One line each, dated.\n", created, root)
    (root / "projects").mkdir(exist_ok=True)
    (root / "general").mkdir(exist_ok=True)
    if demo:
        demo_content(root, created)
    remember_workspace(root)
    return created


def add_project(root: Path, slug: str, name: str, category: str = "work", status: str = "Active") -> list[str]:
    parts = slug.split("/")
    if not all(SLUG_RE.match(p) for p in parts) or len(parts) > 3:
        raise SystemExit(f"'{slug}' is not a valid slug. Use lowercase words joined by hyphens, e.g. acme-plumbing "
                         "(a sub-project is parent/child, e.g. acme-plumbing/website).")
    if len(parts) > 1 and not any(r["slug"] == parts[0] for r in parse_registry(root / "memory" / "reference" / "projects.md")):
        raise SystemExit(f"Register the parent '{parts[0]}' first.")
    name = " ".join(name.replace("|", "/").split())
    if not SLUG_RE.match(category):
        raise SystemExit(f"'{category}' is not a valid category. Use lowercase-kebab-case.")
    registry = root / "memory" / "reference" / "projects.md"
    if not registry.is_file():
        raise SystemExit("No registry found. Run 'init' first.")
    if any(r["slug"] == slug for r in parse_registry(registry)):
        raise SystemExit(f"Slug '{slug}' is already registered.")
    created: list[str] = []
    text = registry.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    text += f"| {slug} | {name} | {category}/{slug}/ | {slug}/ | {status} |\n"
    registry.write_text(text, encoding="utf-8")
    write_new(root / "memory" / category / slug / "context.md",
              render("context.md.tmpl", name=name, slug=slug, date=today()), created, root)
    write_new(root / "memory" / category / slug / "log.md",
              render("log.md.tmpl", name=name, date=today()), created, root)
    (root / "projects" / slug).mkdir(parents=True, exist_ok=True)
    created.append(f"projects/{slug}/")
    # Keep the master index in step with the registry.
    idx = root / "memory" / "_index.md"
    if idx.is_file():
        body = idx.read_text(encoding="utf-8")
        if not body.endswith("\n"):
            body += "\n"
        body += f"| {name} | memory/{category}/{slug}/context.md | {today()} |\n"
        idx.write_text(body, encoding="utf-8")
    return created


def demo_content(root: Path, created: list[str]) -> None:
    """A tiny fictional workspace. Every name here is invented. Never overwrites."""
    d = stamp()
    registered = {r["slug"] for r in parse_registry(root / "memory" / "reference" / "projects.md")}
    if "sunrise-bakery" not in registered:
        created += add_project(root, "sunrise-bakery", "Sunrise Bakery (demo client)")
    if "newsletter" not in registered:
        created += add_project(root, "newsletter", "Weekly Newsletter (demo)")
    ctx = root / "memory" / "work" / "sunrise-bakery" / "context.md"
    if ctx.is_file() and "{{" not in ctx.read_text(encoding="utf-8") and "(one paragraph" in ctx.read_text(encoding="utf-8"):
        ctx.write_text(DEMO_CONTEXT, encoding="utf-8")  # only replaces the untouched template we just wrote
    docs = root / "projects" / "sunrise-bakery" / "documents"
    sheets = root / "projects" / "sunrise-bakery" / "spreadsheets"
    write_new(docs / f"{d}-holiday-campaign-proposal.md",
              "# Holiday campaign proposal (demo)\n\nThis stands in for the Word document you would hand to a client.\n", created, root)
    write_new(sheets / f"{d}-preorder-prices.csv",
              "item,price\nsourdough loaf,8.00\ncinnamon buns (6),14.00\n", created, root)
    write_new(root / "projects" / "newsletter" / "documents" / f"{d}-issue-01-draft.md",
              "# Issue 1 (demo)\n\nA finished draft for a human to read.\n", created, root)
    write_new(root / "general" / "documents" / f"{d}-random-idea.md",
              "# Idea (demo)\n\nOne-off work that belongs to no project.\n", created, root)


DEMO_CONTEXT = """---
name: sunrise-bakery-context
description: Current truth for the fictional Sunrise Bakery demo client.
---
# Sunrise Bakery (demo client)
**Last Updated:** demo

## Overview
Fictional neighbourhood bakery. We run their website refresh and a holiday order campaign.

## Current state
- Website copy approved; waiting on new product photos.
- Holiday pre-order campaign drafted (see the proposal in projects/sunrise-bakery/documents/).

## Decisions
- Pre-orders open on the 1st of the month; the owner approves all prices.

## Next steps
- [ ] Get photos from the owner
- [ ] Send campaign preview for approval
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="create a new workspace (never overwrites)")
    p_init.add_argument("workspace")
    p_init.add_argument("--demo", action="store_true", help="also add a small fictional sample")
    p_add = sub.add_parser("add-project", help="register a project and create its folders")
    p_add.add_argument("workspace")
    p_add.add_argument("slug")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--category", default="work")
    sub.add_parser("where", help="print the workspace folder remembered on this computer")
    args = ap.parse_args()
    if args.cmd == "where":
        ws = remembered_workspace()
        print(ws if ws else "No workspace remembered on this computer yet. Ask the user which folder to use.")
        return 0 if ws else 1
    root = Path(args.workspace).expanduser().resolve()
    if args.cmd == "init":
        created = init(root, args.demo)
        print(f"Workspace ready at {root}")
    else:
        created = add_project(root, args.slug, args.name, args.category)
        print(f"Registered '{args.slug}' in {root}")
    for c in created:
        print(f"  created {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
