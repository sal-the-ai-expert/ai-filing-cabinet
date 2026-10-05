---
name: two-folder-workspace
description: Keeps a business owner's AI work in one folder they choose - finished Word/Excel/PDF files for people under projects/, short markdown notes for the AI under memory/. Use at the start of any session that will create files, whenever a file is generated, when the user wants to organise or clean up their files, or check the workspace for drift. Never deletes files; anything to remove is queued for the user.
license: MIT
compatibility: Needs Python 3.9+ and a folder the AI can write to. The optional guard needs Claude Code hooks.
metadata:
  version: "0.2.0"
allowed-tools: Read Write Edit
---

# Two-folder workspace

Two folders, two audiences, one place. People get finished files they can open in two clicks. The AI gets short plain-text notes it can read fast. Everything lives in the one folder the user picks.

```
<the user's folder>/
├── CLAUDE.md        rules (the AI reads this first)
├── memory/          for the AI: markdown only
│   ├── _index.md
│   ├── reference/projects.md    registry of project slugs
│   └── <category>/<slug>/context.md + log.md
├── projects/        for people: .docx .xlsx .pptx .pdf images
│   └── <slug>/<type>/YYYYMMDD-name.ext
├── general/         one-off work with no project
└── _review/         things waiting for the user's decision (cleanup plans, delete queue)
```

`${CLAUDE_SKILL_DIR}` is this skill's folder. Scripts live in `${CLAUDE_SKILL_DIR}/scripts/`.

## Three rules that never bend

1. **One folder, chosen by the user.** Every file you make goes into that folder. Never leave a file only in a chat download, a sandbox (`outputs/`, `/tmp`, a cloud session's scratch space) or a memory folder somewhere else.
2. **Write-through.** Save each file the moment you make it, in the same turn, then say the exact path. A preview or download link in chat is not a save.
3. **Never delete, never overwrite the user's files.** Not their files, not junk, not duplicates. Move them into `_review/quarantine/` (keep the path, and check nothing is already there) and add a line to `_review/DELETE-QUEUE.md`. The user decides and deletes. To change a finished file, save a new dated version (`-v2`) next to it. The only files you rewrite in place are your own memory notes (`context.md`, `_index.md`).

## Step 1: Find the folder (first thing, every session)

1. Look for a workspace: a `.two-folder.json` in the current folder or any parent, then `python3 "${CLAUDE_SKILL_DIR}/scripts/scaffold.py" where`, then a path the user has given in this conversation.
2. **If none is found, stop and ask before doing anything else:**
   > "Before we start: which folder should I keep everything in? I'll save every file I make there, keep my notes for you there, and help tidy what's already in it. A folder you already back up or sync works best."
   Do not pick a location yourself, and do not create files until they answer.
3. If you can't reach the folder (for example a cloud session that isn't linked to their computer), say so before making any file, and ask them to connect it. If they want the file anyway, give it to them clearly labelled "not saved to your folder yet", keep a list of what still needs saving and where, and save it the moment the folder is reachable. Never say a file is saved when it isn't.

## Step 2: Set it up (first run only)

1. Run `python3 "${CLAUDE_SKILL_DIR}/scripts/scaffold.py" init "<folder>"`. It never overwrites, so it is safe on a folder that already has files. It also remembers the folder on this computer.
2. If the folder already had files, offer a tidy-up (Step 5). Don't start moving things uninvited.
3. Ask what their main projects or clients are, and register each one: `scaffold.py add-project "<folder>" <slug> --name "<Display name>"`.
4. Tell them in plain words what was created.

## Step 3: Every time you make a file

1. Read `memory/_index.md`, then `memory/reference/projects.md`.
2. Find the project slug. No match: ask which project it belongs to (or register a new one). Never invent a folder.
3. Pick the type folder from the extension (`memory/reference/file-routing.md`).
4. Save straight to `projects/<slug>/<type>/YYYYMMDD-name.ext` (one-offs: `general/<type>/`).
5. Tell the user: "Saved to projects/acme/documents/20261004-quote.docx."
6. If something durable changed, overwrite that project's `context.md` and append one dated entry to `log.md`.

## Step 4: Keep memory current

- `context.md` is what's true now: overwrite it. `log.md` is history: append only, and only when something changed.
- Save memory after each substantial task, not only at the end. Sessions can end without warning.
- Read before you overwrite.

## Step 5: Cleaning up (only with the user's go-ahead)

1. `python3 "${CLAUDE_SKILL_DIR}/scripts/audit.py" "<folder>"` reads only. Summarise worst first.
2. `python3 "${CLAUDE_SKILL_DIR}/scripts/cleanup.py" plan "<folder>"` writes `_review/CLEANUP-PLAN.md` and changes nothing else. Show the user the plan in plain words.
3. Only after they approve: `cleanup.py apply "<folder>"` (or `--only 1,4,7` for part of it). It moves files, never overwrites, skips anything that changed, and logs every move in `_review/MOVE-LOG.md` so it can be undone.
4. Junk and duplicates end up in `_review/quarantine/`, listed in `_review/DELETE-QUEUE.md`. Point the user to that list. They delete; you don't.
5. Items marked "needs your decision" stay where they are until the user says where they go.

## Never

- Delete or empty any file in the workspace, by any method: shell commands (`rm`, `find -delete`, `git clean`, `truncate`), moving to the trash, or from inside a script.
- Overwrite a user's file. (Rewriting your own memory notes is fine.)
- Put anything at the workspace root except CLAUDE.md, memory/, projects/, general/, _review/ and the `.two-folder.json` settings file. Never move or quarantine `.two-folder.json`: the guard and the workspace lookup depend on it.
- Leave a file loose in a project folder, or put a finished file in memory/.
- Create a folder for a project that isn't in the registry.
- Move or rename the user's files without their go-ahead.

## Limits

The optional guard hook stops Claude's Write and Edit tools creating files in the wrong place, and blocks the common shell delete commands that target the workspace. It is a seatbelt, not a sandbox: it can't see a delete hidden inside a script or a variable it can't resolve, or what other apps do. These rules are what keep files safe; the guard and the audit catch slips.
