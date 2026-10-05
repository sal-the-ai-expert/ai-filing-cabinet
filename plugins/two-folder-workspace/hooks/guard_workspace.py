#!/usr/bin/env python3
"""PreToolUse guard: keeps Claude's file writes inside the two-folder rules.

Claude Code passes the tool call as JSON on stdin. Exit code 2 blocks the write and
shows the message on stderr to Claude, so it can pick a better place. Any other
outcome lets the write through. The guard is deliberately fail-open: if anything
unexpected happens it allows the write rather than getting in your way.

It only acts inside a folder that contains a .two-folder.json file.
It covers Claude's Write and Edit tools, and blocks shell commands that would delete files
in the workspace (rm, rmdir, unlink, find -delete, git clean ...). Deleting is the user's call:
the AI moves the file to _review/ and lists it in _review/DELETE-QUEUE.md instead.
It cannot see files written or deleted from inside other programs (for example a Python script).

NOTE: the constants below intentionally duplicate skills/.../scripts/common.py so this
hook stays self-contained. tests/test_workspace.py checks that they stay in sync.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sys
from pathlib import Path

CONFIG_NAME = ".two-folder.json"
TYPE_FOLDERS = {"documents", "spreadsheets", "presentations", "pdfs", "designs", "code", "exports"}
DELIVERABLE_EXTS = {
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".key", ".pages", ".numbers",
}
REVIEW_DIR = "_review"
DEFAULT_ALLOW = ["CLAUDE.md", "memory", "projects", "general", REVIEW_DIR, CONFIG_NAME]
DEFAULT_ENFORCE = ["root", "loose", "dateprefix", "memory-split", "no-delete"]
DELETE_VERBS = {"rm", "rmdir", "unlink", "shred", "srm", "trash", "truncate",
                "del", "erase", "rd", "remove-item", "ri"}
# Words after which the next word is a command name, not an argument.
PREFIXES = {"sudo", "env", "command", "builtin", "exec", "nohup", "time", "nice", "do", "then", "else",
            "xargs", "!", "{", "(", "if", "while", "until"}
OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "{", "}", "\n"}
DELETE_MSG = ("Blocked: deleting files in this workspace is the user's decision, not yours. "
              "Move the file into _review/quarantine/ (keep its path) and add a line to "
              "_review/DELETE-QUEUE.md saying what it is and why it could go. "
              "Then tell the user it is queued for them to review.")
DATE_RE = re.compile(r"^\d{8}-")


def find_root(start: Path):
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    return None


def check(rel_parts: list[str], cfg: dict) -> str | None:
    """Return a message if the write should be blocked, else None."""
    enforce = set(cfg.get("enforce", DEFAULT_ENFORCE))
    allow = set(cfg.get("root_allow", DEFAULT_ALLOW))
    undated_ok = cfg.get("undated_ok", [])
    tfolders = TYPE_FOLDERS | set(cfg.get("extra_type_folders", []))
    name = rel_parts[-1]
    rel = "/".join(rel_parts)

    if len(rel_parts) == 1:
        if "root" in enforce and name not in allow and not name.startswith("."):
            return (f"Blocked: '{name}' would sit at the top of the workspace. Finished files go in "
                    "projects/<project>/<type>/ (with a YYYYMMDD- name); AI notes go in memory/. "
                    "Ask the user which project it belongs to if unsure.")
        return None

    top = rel_parts[0]

    if "root" in enforce and top not in allow and not top.startswith("."):
        return (f"Blocked: '{top}/' is not an allowed top-level folder. The workspace root holds only "
                f"{', '.join(sorted(allow))}. Put this under projects/<project>/<type>/ or memory/. "
                "Ask the user which project it belongs to if unsure.")

    if top == REVIEW_DIR:
        return None

    if top == "memory":
        if "memory-split" in enforce and Path(name).suffix.lower() in DELIVERABLE_EXTS:
            return (f"Blocked: '{name}' is a finished human file. Memory is for plain-text notes. "
                    "Save it under projects/<project>/<type>/ instead.")
        return None

    if top in ("projects", "general"):
        # Find where the type folder starts.
        start = 2 if top == "projects" else 1
        if top == "projects" and len(rel_parts) == 2:
            if "loose" in enforce:
                return f"Blocked: '{name}' would be loose in projects/. Put it in projects/<project>/<type>/."
            return None
        type_idx = next((i for i in range(start, len(rel_parts) - 1) if rel_parts[i] in tfolders), None)
        if type_idx is None:
            if "loose" in enforce:
                where = "/".join(rel_parts[:-1])
                return (f"Blocked: '{name}' would sit loose in {where}/. Use a type folder: "
                        "documents, spreadsheets, presentations, pdfs, designs, code or exports.")
            return None
        inner = rel_parts[type_idx + 1:-1]
        bundled = rel_parts[type_idx] == "code" and inner or any(DATE_RE.match(p) for p in inner)
        if "dateprefix" in enforce and not bundled and not DATE_RE.match(name) and not name.startswith("."):
            if not any(fnmatch.fnmatch(rel, g) for g in undated_ok):
                return (f"Blocked: '{name}' needs a date prefix, e.g. YYYYMMDD-{name}. "
                        "(Living templates can be listed under undated_ok in .two-folder.json.)")
    return None


def load_cfg(root: Path) -> dict:
    try:
        cfg = json.loads((root / CONFIG_NAME).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    for key in ("root_allow", "enforce", "undated_ok", "extra_type_folders"):
        if not (isinstance(cfg.get(key, []), list) and all(isinstance(v, str) for v in cfg.get(key, []))):
            cfg.pop(key, None)
    return cfg


def _tokens(command: str) -> list[str]:
    lex = shlex.shlex(command.replace("\n", " ; "), posix=(os.name != "nt"), punctuation_chars=";&|()")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return command.split()


def _delete_targets(command: str) -> list[str] | None:
    """Return the path arguments of any delete-like command in a shell line, or None if there is none.

    Best effort: it recognises common forms (rm, /bin/rm, \\rm, sudo/env/xargs rm, rm inside loops and
    if-blocks, find -delete / -exec rm, git rm / git clean / git reset --hard, truncate, Windows del/rd/
    Remove-Item). It is a seatbelt, not a sandbox: a program can still delete files on its own.
    """
    toks = _tokens(command)
    found = False
    targets: list[str] = []
    at_cmd = True
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in OPERATORS or t == "\\n":
            at_cmd = True
            i += 1
            continue
        if at_cmd:
            word = t.lstrip("\\")
            base = os.path.basename(word).lower()
            if base in PREFIXES or (base.startswith("-") and toks[i - 1:i] and os.path.basename(toks[i - 1]) == "xargs"):
                i += 1
                continue
            at_cmd = False
            args: list[str] = []
            j = i + 1
            while j < len(toks) and toks[j] not in OPERATORS and toks[j] != "\\n":
                args.append(toks[j])
                j += 1
            if base in DELETE_VERBS:
                found = True
                targets += [a for a in args if not a.startswith("-") or os.name == "nt" and a.startswith("/")]
            elif base == "git" and args and (args[0] in ("rm", "clean") or args[:2] == ["reset", "--hard"]):
                found = True
                targets += [a for a in args[1:] if not a.startswith("-")]
            elif base == "find" and ("-delete" in args or any(a in ("-exec", "-execdir", "-ok") and k + 1 < len(args)
                                                             and os.path.basename(args[k + 1]).lower() in DELETE_VERBS
                                                             for k, a in enumerate(args))):
                found = True
                paths = []
                for a in args:
                    if a.startswith("-") or a in ("!", "("):
                        break
                    paths.append(a)
                targets += paths or ["."]
            i = j
            continue
        i += 1
    return targets if found else None


def _remembered() -> Path | None:
    pf = os.environ.get("TWO_FOLDER_POINTER_FILE") or str(Path.home() / ".config" / "two-folder-workspace" / "workspace.txt")
    try:
        text = Path(pf).read_text(encoding="utf-8").strip()
        return Path(os.path.realpath(text)) if text else None
    except OSError:
        return None


def check_delete(command: str, cwd: Path) -> str | None:
    """Return a message if a shell command would delete files inside (or containing) a workspace."""
    targets = _delete_targets(command or "")
    if targets is None:
        return None
    remembered = _remembered()
    candidates: list[Path] = []
    for tok in targets:
        if "$" in tok or "`" in tok:
            candidates.append(cwd)  # can't resolve a variable: judge by where we are
            continue
        p = Path(os.path.expanduser(tok.replace("*", "x").replace("?", "x")))
        candidates.append(p if p.is_absolute() else cwd / p)
    if not targets:
        candidates.append(cwd)
    for c in candidates:
        real = Path(os.path.realpath(c))
        root = find_root(real)
        if root is None and remembered is not None:
            try:
                remembered.relative_to(real)  # deleting a folder that contains the workspace
                root = remembered
            except ValueError:
                pass
        if root is not None and "no-delete" in set(load_cfg(root).get("enforce", DEFAULT_ENFORCE)):
            return DELETE_MSG
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
        tool_input = data.get("tool_input") or {}
        if data.get("tool_name") in ("Bash", "PowerShell"):
            message = check_delete(str(tool_input.get("command") or ""), Path(data.get("cwd") or os.getcwd()))
            if message:
                print(message, file=sys.stderr)
                return 2
            return 0
        target = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
        if not target:
            return 0
        path = Path(target)
        if not path.is_absolute():
            path = Path(data.get("cwd") or os.getcwd()) / path
        path = Path(os.path.realpath(path))
        root = find_root(path)
        if root is None:
            return 0
        root = Path(os.path.realpath(root))
        try:
            rel_parts = list(path.relative_to(root).parts)
        except ValueError:
            return 0
        if not rel_parts:
            return 0
        if os.path.lexists(path):
            return 0  # editing or updating a file that already exists: its place was decided before
        message = check(rel_parts, load_cfg(root))
        if message:
            print(message, file=sys.stderr)
            return 2
    except Exception:  # fail open
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
