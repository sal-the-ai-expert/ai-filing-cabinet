#!/usr/bin/env python3
"""Plan and apply a tidy-up of a two-folder workspace. It never deletes anything.

Usage:
    python3 cleanup.py plan  /path/to/workspace          # writes a plan, changes nothing else
    python3 cleanup.py apply /path/to/workspace [--only 1,4,7]   # moves only approved items

How it stays safe:
- `plan` only reads the workspace and writes the plan into `_review/`.
- `apply` only MOVES files, never overwrites, and skips anything that changed since the plan.
- Junk and duplicates are not deleted. They are moved into `_review/quarantine/` and listed in
  `_review/DELETE-QUEUE.md`. Deleting is the user's decision, done by the user.
- Every move is written to `_review/MOVE-LOG.md` so it can be undone by moving the file back.
- Anything the script can't place with confidence is listed as "needs your decision" and left alone.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import audit  # noqa: E402
from common import DELETE_QUEUE, REVIEW_DIR, load_config, parse_registry, type_for_ext  # noqa: E402

PLAN_JSON = "cleanup-plan.json"
PLAN_MD = "CLEANUP-PLAN.md"
MOVE_LOG = "MOVE-LOG.md"


def _type_folder(name: str) -> str:
    return type_for_ext(Path(name).suffix) or "exports"


def _fingerprint(path: Path) -> list:
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def _memory_to_project(root: Path, rel: str) -> str | None:
    """memory/<memory_path>/file.pdf -> the registry row whose memory path contains it."""
    rows = parse_registry(root / "memory" / "reference" / "projects.md")
    inner = rel[len("memory/"):]
    best = None
    for r in rows:
        mp = r["memory_path"].strip("/")
        if mp and (inner == mp or inner.startswith(mp + "/")) and r["projects_path"]:
            if best is None or len(mp) > len(best[0]):
                best = (mp, r["projects_path"].strip("/"))
    return best[1] if best else None


def build_plan(root: Path) -> dict:
    items: list[dict] = []
    decisions: list[dict] = []

    def move(src: str, dst: str, why: str, kind: str = "move") -> None:
        p = root / src
        if not p.exists():
            return
        items.append({"id": len(items) + 1, "kind": kind, "src": src, "dst": dst,
                      "why": why, "fingerprint": _fingerprint(p)})

    for f in audit(root):
        rel = f.path
        name = Path(rel).name
        if f.code == "JUNK":
            if (root / rel).is_file() and not (root / rel).is_symlink():
                move(rel, f"{REVIEW_DIR}/quarantine/{rel}", "Junk file. Queued for you to delete.", kind="queue-delete")
            else:
                decisions.append({"path": rel, "why": "Looks like junk but isn't a plain file. Check it yourself."})
        elif f.code == "LOOSE_FILE":
            parent = str(Path(rel).parent)
            if parent == "projects":
                decisions.append({"path": rel, "why": "Loose file directly in projects/. Which project is it for?"})
            else:
                move(rel, f"{parent}/{_type_folder(name)}/{name}", "Loose file: moved into its type folder.")
        elif f.code == "DELIVERABLE_IN_MEMORY":
            target = _memory_to_project(root, rel)
            if target:
                move(rel, f"projects/{target}/{_type_folder(name)}/{name}",
                     "Finished file was inside memory/: moved to its project.")
            else:
                decisions.append({"path": rel, "why": "Finished file inside memory/ with no matching project in the registry. Which project?"})
        elif f.severity == "problem":
            decisions.append({"path": rel, "why": f"{f.message} Suggested fix: {f.fix}"})

    # Two moves may not land on the same destination, and never on an existing file.
    seen: set[str] = set()
    for it in items:
        if os.path.lexists(root / it["dst"]) or it["dst"] in seen:
            it["kind"] = "conflict"
            it["why"] += " A file already exists at the destination, so this is left for you to decide."
        seen.add(it["dst"])

    return {"workspace": str(root), "created": dt.datetime.now().isoformat(timespec="seconds"),
            "items": items, "decisions": decisions}


def write_plan(root: Path, plan: dict) -> Path:
    review = root / REVIEW_DIR
    review.mkdir(exist_ok=True)
    (review / PLAN_JSON).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    lines = [f"# Cleanup plan ({plan['created']})", "",
             "Nothing has been changed yet. Approve all of it, or only some item numbers,",
             "then run: `cleanup.py apply <workspace>` or `cleanup.py apply <workspace> --only 1,4,7`.",
             "Nothing is ever deleted. Junk is moved to `_review/quarantine/` and listed in "
             f"`_review/{DELETE_QUEUE}` for you to delete yourself.", ""]
    groups = [("move", "Moves"), ("queue-delete", "Queued for you to delete (moved to quarantine, not deleted)"),
              ("conflict", "Left alone: destination already exists")]
    for kind, title in groups:
        rows = [i for i in plan["items"] if i["kind"] == kind]
        if not rows:
            continue
        lines += [f"## {title} ({len(rows)})", "", "| # | From | To | Why |", "|-|-|-|-|"]
        lines += [f"| {i['id']} | `{i['src']}` | `{i['dst']}` | {i['why']} |" for i in rows]
        lines.append("")
    if plan["decisions"]:
        lines += [f"## Needs your decision ({len(plan['decisions'])})", ""]
        lines += [f"- `{d['path']}`: {d['why']}" for d in plan["decisions"]]
        lines.append("")
    if not plan["items"] and not plan["decisions"]:
        lines.append("Nothing to tidy. The workspace follows the rules.")
    out = review / PLAN_MD
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _append(path: Path, header: str, line: str) -> None:
    new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if new:
            fh.write(header)
        fh.write(line)


def apply_plan(root: Path, only: set[int] | None) -> list[str]:
    plan_path = root / REVIEW_DIR / PLAN_JSON
    if not plan_path.is_file():
        raise SystemExit("No plan found. Run `cleanup.py plan` first and review it.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    review = root / REVIEW_DIR
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    report: list[str] = []
    for it in plan["items"]:
        if it["kind"] not in ("move", "queue-delete") or (only and it["id"] not in only):
            continue
        src, dst = root / it["src"], root / it["dst"]
        if not src.exists():
            report.append(f"skipped #{it['id']}: {it['src']} is gone")
            continue
        if _fingerprint(src) != it["fingerprint"]:
            report.append(f"skipped #{it['id']}: {it['src']} changed since the plan; run plan again")
            continue
        if os.path.lexists(dst):
            report.append(f"skipped #{it['id']}: {it['dst']} already exists")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(dst):  # re-check right before the move (a sync app may have just added it)
            report.append(f"skipped #{it['id']}: {it['dst']} appeared during the move")
            continue
        os.rename(src, dst)  # a move, never a delete
        _append(review / MOVE_LOG, "# Move log\n\nTo undo a line, move the file from \"To\" back to \"From\".\n\n"
                "| When | From | To |\n|-|-|-|\n", f"| {stamp} | `{it['src']}` | `{it['dst']}` |\n")
        if it["kind"] == "queue-delete":
            _append(review / DELETE_QUEUE, "# Delete queue\n\nThese files were set aside, not deleted. "
                    "Check each one, then delete it yourself or move it back.\n\n",
                    f"- [ ] `{it['dst']}` (was `{it['src']}`, queued {stamp}): {it['why']}\n")
        report.append(f"moved #{it['id']}: {it['src']} -> {it['dst']}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan", help="write a cleanup plan to _review/ (changes nothing else)")
    p_plan.add_argument("workspace")
    p_apply = sub.add_parser("apply", help="carry out an approved plan (moves only, never deletes)")
    p_apply.add_argument("workspace")
    p_apply.add_argument("--only", help="comma-separated item numbers to apply")
    args = ap.parse_args()
    root = Path(args.workspace).expanduser().resolve()
    if not (root / "memory").is_dir() and not (root / "projects").is_dir():
        print(f"{root} doesn't look like a two-folder workspace. Run scaffold.py init first.", file=sys.stderr)
        return 2
    load_config(root)
    if args.cmd == "plan":
        plan = build_plan(root)
        out = write_plan(root, plan)
        kinds = [i["kind"] for i in plan["items"]]
        print(f"Plan written to {out}")
        print(f"  {kinds.count('move')} moves, {kinds.count('queue-delete')} queued for deletion, "
              f"{kinds.count('conflict')} conflicts, {len(plan['decisions'])} need a decision. Nothing changed yet.")
    else:
        only = {int(x) for x in args.only.split(",")} if args.only else None
        for line in apply_plan(root, only):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
