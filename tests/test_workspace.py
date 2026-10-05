"""Run with:  python3 -m unittest discover -s tests -v"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Keep the "remembered workspace" pointer out of the real home folder during tests.
os.environ["TWO_FOLDER_POINTER_FILE"] = str(Path(tempfile.mkdtemp()) / "workspace.txt")
PLUGIN = REPO / "plugins" / "two-folder-workspace"
SCRIPTS = PLUGIN / "skills" / "two-folder-workspace" / "scripts"
HOOK = PLUGIN / "hooks" / "guard_workspace.py"
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import audit as audit_mod  # noqa: E402
import scaffold  # noqa: E402
import cleanup  # noqa: E402


def run_hook(tool_input, cwd, tool_name="Write"):
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input, "cwd": str(cwd)})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "ws"

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_creates_locked_root(self):
        scaffold.init(self.root, demo=False)
        names = {p.name for p in self.root.iterdir()}
        self.assertEqual(names, {"CLAUDE.md", "memory", "projects", "general", common.CONFIG_NAME})

    def test_init_never_overwrites(self):
        scaffold.init(self.root, demo=False)
        (self.root / "CLAUDE.md").write_text("mine", encoding="utf-8")
        scaffold.init(self.root, demo=False)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "mine")

    def test_add_project_and_duplicate_rejected(self):
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme-plumbing", "Acme Plumbing")
        self.assertTrue((self.root / "memory/work/acme-plumbing/context.md").is_file())
        self.assertTrue((self.root / "projects/acme-plumbing").is_dir())
        with self.assertRaises(SystemExit):
            scaffold.add_project(self.root, "acme-plumbing", "Again")

    def test_bad_slug_rejected(self):
        scaffold.init(self.root, demo=False)
        for bad in ("Acme Plumbing", "acme_plumbing", "ACME"):
            with self.assertRaises(SystemExit):
                scaffold.add_project(self.root, bad, "x")

    def test_demo_workspace_audits_clean(self):
        scaffold.init(self.root, demo=True)
        problems = [f for f in audit_mod.audit(self.root) if f.severity in ("problem", "warning")]
        self.assertEqual(problems, [], [f"{f.code} {f.path}" for f in problems])


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "ws"
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme", "Acme")

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self):
        return {f.code for f in audit_mod.audit(self.root)}

    def touch(self, rel, text="x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_root_extra(self):
        self.touch("Sales/notes.txt")
        self.assertIn("ROOT_EXTRA", self.codes())

    def test_loose_file_in_project(self):
        self.touch("projects/acme/proposal.docx")
        self.assertIn("LOOSE_FILE", self.codes())

    def test_unregistered_project(self):
        (self.root / "projects" / "mystery").mkdir()
        self.assertIn("UNREGISTERED_PROJECT", self.codes())

    def test_near_duplicate(self):
        (self.root / "projects" / "acmes").mkdir()
        self.assertIn("NEAR_DUPLICATE", self.codes())

    def test_no_date_prefix(self):
        self.touch("projects/acme/documents/proposal.md")
        self.assertIn("NO_DATE_PREFIX", self.codes())

    def test_dated_file_is_fine(self):
        self.touch("projects/acme/documents/20260101-proposal.md")
        self.assertNotIn("NO_DATE_PREFIX", self.codes())

    def test_deliverable_in_memory(self):
        self.touch("memory/work/acme/report.pdf")
        self.assertIn("DELIVERABLE_IN_MEMORY", self.codes())

    def test_memory_file_in_human_tree(self):
        self.touch("projects/acme/documents/20260101-context.md")
        self.touch("projects/acme/documents/context.md")
        self.assertIn("MEMORY_FILE_IN_HUMAN_TREE", self.codes())

    def test_unknown_folder_and_junk(self):
        (self.root / "projects/acme/drafts").mkdir()
        self.touch(".DS_Store")
        self.assertIn("UNKNOWN_FOLDER", self.codes())
        self.assertIn("JUNK", self.codes())

    def test_undated_ok(self):
        cfg = json.loads((self.root / common.CONFIG_NAME).read_text(encoding="utf-8"))
        cfg["undated_ok"] = ["projects/acme/documents/template.md"]
        (self.root / common.CONFIG_NAME).write_text(json.dumps(cfg), encoding="utf-8")
        self.touch("projects/acme/documents/template.md")
        self.assertNotIn("NO_DATE_PREFIX", self.codes())

    def test_audit_is_read_only(self):
        self.touch("Sales/notes.txt")
        before = sorted(str(p) for p in self.root.rglob("*"))
        audit_mod.audit(self.root)
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))

    def test_cli_exit_codes(self):
        script = str(SCRIPTS / "audit.py")
        ok = subprocess.run([sys.executable, script, str(self.root)], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.touch("Sales/notes.txt")
        bad = subprocess.run([sys.executable, script, str(self.root)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "ws"
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme", "Acme")

    def tearDown(self):
        self.tmp.cleanup()

    def blocked(self, rel):
        return run_hook({"file_path": str(self.root / rel)}, self.root).returncode == 2

    def test_allows_good_paths(self):
        for rel in ("projects/acme/documents/20260101-x.docx", "memory/work/acme/context.md",
                    "general/documents/20260101-x.md", "CLAUDE.md"):
            self.assertFalse(self.blocked(rel), rel)

    def test_blocks_bad_paths(self):
        for rel in ("notes.md", "Sales/x.md", "projects/x.md", "projects/acme/x.docx",
                    "projects/acme/documents/x.docx", "memory/work/acme/report.pdf",
                    "projects/acme/drafts/20260101-x.md"):
            self.assertTrue(self.blocked(rel), rel)

    def test_ignores_files_outside_a_workspace(self):
        other = Path(self.tmp.name) / "elsewhere" / "x.txt"
        self.assertEqual(run_hook({"file_path": str(other)}, Path(self.tmp.name)).returncode, 0)

    def test_fail_open_on_garbage(self):
        r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)

    def test_dotdot_escape_is_resolved(self):
        rel = "projects/acme/documents/../../../loose.md"
        self.assertTrue(self.blocked(rel))

    def test_message_goes_to_stderr(self):
        r = run_hook({"file_path": str(self.root / "notes.md")}, self.root)
        self.assertIn("Blocked", r.stderr)


class RegressionTests(unittest.TestCase):
    """Cases found by the independent review."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "ws"
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme", "Acme")

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self):
        return {f.code for f in audit_mod.audit(self.root)}

    def test_demo_never_overwrites_existing_notes(self):
        target = self.root / "memory/work/sunrise-bakery/context.md"
        target.parent.mkdir(parents=True)
        target.write_text("MINE", encoding="utf-8")
        scaffold.init(self.root, demo=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "MINE")

    def test_demo_is_idempotent(self):
        scaffold.init(self.root, demo=True)
        scaffold.init(self.root, demo=True)

    def test_loose_file_directly_in_projects(self):
        (self.root / "projects" / "loose.docx").write_text("x", encoding="utf-8")
        self.assertIn("LOOSE_FILE", self.codes())

    def test_dotfiles_and_dotfolders_at_root_are_ignored(self):
        (self.root / ".git").mkdir()
        (self.root / ".gitignore").write_text("x", encoding="utf-8")
        (self.root / ".claude").mkdir()
        self.assertNotIn("ROOT_EXTRA", self.codes())
        for rel in (".claude/settings.json", ".gitignore"):
            self.assertEqual(run_hook({"file_path": str(self.root / rel)}, self.root).returncode, 0, rel)

    def test_bom_config_is_read(self):
        (self.root / common.CONFIG_NAME).write_bytes(b"\xef\xbb\xbf" + json.dumps({"enforce": []}).encode())
        r = run_hook({"file_path": str(self.root / "stray.txt")}, self.root)
        self.assertEqual(r.returncode, 0)

    def test_bad_config_is_reported_not_crashing(self):
        (self.root / common.CONFIG_NAME).write_text('{"root_allow": null}', encoding="utf-8")
        self.assertIn("CONFIG_INVALID", self.codes())
        (self.root / common.CONFIG_NAME).write_text("{not json", encoding="utf-8")
        self.assertIn("CONFIG_INVALID", self.codes())

    def test_non_utf8_registry_does_not_crash(self):
        reg = self.root / "memory/reference/projects.md"
        reg.write_bytes(reg.read_bytes() + b"\xff\xfe junk\n")
        audit_mod.audit(self.root)

    def test_pipe_and_newline_in_name_are_sanitised(self):
        scaffold.add_project(self.root, "beta", "Foo | Bar\n| evil | x | y | z |")
        rows = common.parse_registry(self.root / "memory/reference/projects.md")
        self.assertEqual([r["slug"] for r in rows], ["acme", "beta"])
        self.assertNotIn("MEMORY_FOLDER_MISSING", self.codes())

    def test_registry_row_projects_path_counts_as_registered(self):
        reg = self.root / "memory/reference/projects.md"
        reg.write_text(reg.read_text(encoding="utf-8") + "| acme-old | Old | work/acme/ | acme-plumbing/ | Active |\n", encoding="utf-8")
        (self.root / "projects" / "acme-plumbing").mkdir()
        self.assertNotIn("UNREGISTERED_PROJECT", self.codes())

    def test_nested_sub_projects_are_followed(self):
        scaffold.add_project(self.root, "acme/partners", "Partners")
        scaffold.add_project(self.root, "acme/partners/sterling", "Sterling")
        f = self.root / "projects/acme/partners/sterling/documents/20260101-x.md"
        f.parent.mkdir(parents=True)
        f.write_text("x", encoding="utf-8")
        bad = {"UNKNOWN_FOLDER", "LOOSE_FILE"} & self.codes()
        self.assertEqual(bad, set())

    def test_sub_project_can_be_scaffolded(self):
        scaffold.add_project(self.root, "acme/website", "Acme website")
        self.assertTrue((self.root / "projects/acme/website").is_dir())
        with self.assertRaises(SystemExit):
            scaffold.add_project(self.root, "nope/child", "x")

    def test_new_top_level_folder_blocked(self):
        r = run_hook({"file_path": str(self.root / "Sales/x.md")}, self.root)
        self.assertEqual(r.returncode, 2)

    def test_notebook_path_is_checked(self):
        r = run_hook({"notebook_path": str(self.root / "stray.ipynb")}, self.root)
        self.assertEqual(r.returncode, 2)

    def test_hidden_folders_inside_memory_are_skipped(self):
        p = self.root / "memory/.obsidian/x.png"
        p.parent.mkdir(parents=True)
        p.write_text("x", encoding="utf-8")
        self.assertNotIn("DELIVERABLE_IN_MEMORY", self.codes())

    def test_undated_ok_matches_the_same_in_audit_and_guard(self):
        cfg = {"undated_ok": ["projects/acme/documents/*.docx"]}
        (self.root / common.CONFIG_NAME).write_text(json.dumps(cfg), encoding="utf-8")
        f = self.root / "projects/acme/documents/sub/t.docx"
        f.parent.mkdir(parents=True)
        f.write_text("x", encoding="utf-8")
        audit_ok = "NO_DATE_PREFIX" not in self.codes()
        guard_ok = run_hook({"file_path": str(f)}, self.root).returncode == 0
        self.assertEqual(audit_ok, guard_ok)


class ConsistencyTests(unittest.TestCase):
    def test_hook_constants_match_common(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("guard", HOOK)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertEqual(guard.TYPE_FOLDERS, set(common.TYPE_FOLDERS))
        self.assertEqual(guard.DELIVERABLE_EXTS, common.DELIVERABLE_EXTS)
        self.assertEqual(guard.CONFIG_NAME, common.CONFIG_NAME)
        self.assertEqual(set(guard.DEFAULT_ALLOW), set(common.DEFAULT_CONFIG["root_allow"]))
        self.assertEqual(guard.DATE_RE.pattern, common.DATE_RE.pattern)

    def test_manifests_are_valid_json(self):
        for rel in (".claude-plugin/marketplace.json",
                    "plugins/two-folder-workspace/.claude-plugin/plugin.json",
                    "plugins/two-folder-workspace/hooks/hooks.json"):
            json.loads((REPO / rel).read_text(encoding="utf-8"))

    def test_skill_frontmatter(self):
        text = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: two-folder-workspace\n"))
        self.assertNotIn("claude", text.split("---")[1].split("name:")[1].split("\n")[0].lower())


class FirstRunTests(unittest.TestCase):
    def test_init_remembers_the_folder(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t).resolve() / "ws"
            scaffold.init(root, demo=False)
            self.assertEqual(common.remembered_workspace(), root)

    def test_where_reports_nothing_when_folder_is_gone(self):
        with tempfile.TemporaryDirectory() as t:
            common.remember_workspace(Path(t) / "missing")
            self.assertIsNone(common.remembered_workspace())


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "ws"
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme", "Acme")
        (self.root / "projects/acme/20260101-quote.docx").write_text("q", encoding="utf-8")
        (self.root / "projects/acme/~$lock.docx").write_text("x", encoding="utf-8")
        (self.root / "memory/work/acme/20260101-report.pdf").write_text("r", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")
                      if not str(p.relative_to(self.root)).startswith(common.REVIEW_DIR))

    def test_plan_changes_nothing_outside_review(self):
        before = self.snapshot()
        plan = cleanup.build_plan(self.root)
        cleanup.write_plan(self.root, plan)
        self.assertEqual(before, self.snapshot())
        self.assertTrue((self.root / "_review/CLEANUP-PLAN.md").is_file())

    def test_apply_moves_and_queues_but_never_deletes(self):
        cleanup.write_plan(self.root, cleanup.build_plan(self.root))
        cleanup.apply_plan(self.root, None)
        self.assertTrue((self.root / "projects/acme/documents/20260101-quote.docx").is_file())
        self.assertTrue((self.root / "projects/acme/pdfs/20260101-report.pdf").is_file())
        queued = self.root / "_review/quarantine/projects/acme/~$lock.docx"
        self.assertTrue(queued.is_file(), "junk must be moved to quarantine, not deleted")
        self.assertIn("~$lock.docx", (self.root / "_review/DELETE-QUEUE.md").read_text(encoding="utf-8"))
        self.assertIn("20260101-quote.docx", (self.root / "_review/MOVE-LOG.md").read_text(encoding="utf-8"))

    def test_apply_never_overwrites(self):
        dest = self.root / "projects/acme/documents/20260101-quote.docx"
        dest.parent.mkdir(parents=True)
        dest.write_text("keep me", encoding="utf-8")
        cleanup.write_plan(self.root, cleanup.build_plan(self.root))
        cleanup.apply_plan(self.root, None)
        self.assertEqual(dest.read_text(encoding="utf-8"), "keep me")
        self.assertTrue((self.root / "projects/acme/20260101-quote.docx").is_file())

    def test_apply_skips_files_changed_since_the_plan(self):
        cleanup.write_plan(self.root, cleanup.build_plan(self.root))
        src = self.root / "projects/acme/20260101-quote.docx"
        src.write_text("edited after the plan, longer", encoding="utf-8")
        cleanup.apply_plan(self.root, None)
        self.assertTrue(src.is_file())

    def test_apply_only_selected_items(self):
        plan = cleanup.build_plan(self.root)
        cleanup.write_plan(self.root, plan)
        first = plan["items"][0]
        cleanup.apply_plan(self.root, {first["id"]})
        moved = [i for i in plan["items"] if (self.root / i["dst"]).exists()]
        self.assertEqual([i["id"] for i in moved], [first["id"]])

    def test_scripts_contain_no_delete_calls(self):
        banned = ("os.remove", "os.unlink", ".unlink(", "rmtree", "os.rmdir", ".rmdir(", "send2trash")
        for script in list(SCRIPTS.glob("*.py")) + [HOOK]:
            text = script.read_text(encoding="utf-8")
            for b in banned:
                self.assertNotIn(b, text, f"{script.name} contains {b}")


class NoDeleteHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "ws"
        scaffold.init(self.root, demo=False)

    def tearDown(self):
        self.tmp.cleanup()

    def bash(self, command, cwd):
        return run_hook({"command": command}, cwd, tool_name="Bash").returncode

    def test_blocks_deletes_inside_workspace(self):
        for cmd in ("rm projects/x.docx", "rm -rf general", "cd projects && rm a.pdf",
                    "find . -name '*.tmp' -delete", "git clean -fd", "ls | xargs rm"):
            self.assertEqual(self.bash(cmd, self.root), 2, cmd)

    def test_blocks_delete_by_absolute_path_from_outside(self):
        self.assertEqual(self.bash(f"rm '{self.root}/projects/x.pdf'", Path(self.tmp.name)), 2)

    def test_allows_other_commands_and_outside_deletes(self):
        self.assertEqual(self.bash("ls -la && grep rm notes.md", self.root), 0)
        self.assertEqual(self.bash("rm /tmp/somewhere-else.txt", Path(self.tmp.name)), 0)

    def test_rule_can_be_turned_off(self):
        cfg = dict(common.DEFAULT_CONFIG)
        cfg["enforce"] = ["root"]
        (self.root / common.CONFIG_NAME).write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(self.bash("rm projects/x.docx", self.root), 0)

    def test_review_folder_is_writable(self):
        r = run_hook({"file_path": str(self.root / "_review/DELETE-QUEUE.md")}, self.root)
        self.assertEqual(r.returncode, 0)


class AuditFlexibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "ws"
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme", "Acme")

    def tearDown(self):
        self.tmp.cleanup()

    def codes(self):
        return {f.code for f in audit_mod.audit(self.root)}

    def test_review_folder_is_allowed_at_root(self):
        (self.root / "_review").mkdir()
        self.assertNotIn("ROOT_EXTRA", self.codes())

    def test_extra_type_folders_from_config(self):
        (self.root / "projects/acme/contracts").mkdir()
        (self.root / "projects/acme/contracts/20260101-msa.docx").write_text("x", encoding="utf-8")
        self.assertIn("UNKNOWN_FOLDER", self.codes())
        cfg = dict(common.DEFAULT_CONFIG)
        cfg["extra_type_folders"] = ["contracts"]
        (self.root / common.CONFIG_NAME).write_text(json.dumps(cfg), encoding="utf-8")
        self.assertNotIn("UNKNOWN_FOLDER", self.codes())

    def test_repo_inside_code_keeps_its_own_names(self):
        repo = self.root / "projects/acme/code/20260101-site"
        (repo / "memory").mkdir(parents=True)
        (repo / "README.md").write_text("x", encoding="utf-8")
        (repo / "memory/context.md").write_text("x", encoding="utf-8")
        codes = self.codes()
        self.assertNotIn("NO_DATE_PREFIX", codes)
        self.assertNotIn("MEMORY_FILE_IN_HUMAN_TREE", codes)


class ReviewFindingsTests(unittest.TestCase):
    """Regression tests for the independent review of the no-delete / cleanup work."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "ws"
        scaffold.init(self.root, demo=False)
        scaffold.add_project(self.root, "acme", "Acme")

    def tearDown(self):
        self.tmp.cleanup()

    def bash(self, command, cwd=None):
        return run_hook({"command": command}, cwd or self.root, tool_name="Bash").returncode

    def test_common_delete_forms_are_blocked(self):
        for cmd in ("echo hi\nrm -rf projects", 'for f in *; do rm "$f"; done',
                    "if true; then rm -rf projects; fi", "/bin/rm -rf projects", "\\rm projects",
                    "command rm projects", "env rm projects", "ls | xargs -0 rm",
                    "find . -execdir rm {} +", "git rm -rf projects", "git reset --hard",
                    "truncate -s 0 CLAUDE.md"):
            self.assertEqual(self.bash(cmd), 2, cmd)

    def test_harmless_commands_inside_workspace_are_allowed(self):
        for cmd in ('git commit -m "docs; rm stale flag"', "rm -rf /tmp/build-cache-xyz",
                    "grep -r rm memory", "python3 audit.py ."):
            self.assertEqual(self.bash(cmd), 0, cmd)

    def test_deleting_a_folder_that_contains_the_workspace_is_blocked(self):
        self.assertEqual(self.bash(f"rm -rf '{self.base}'", cwd=Path("/")), 2)

    def test_junk_named_folder_is_never_queued_as_junk(self):
        d = self.root / "build.tmp"
        d.mkdir()
        (d / "notes.txt").write_text("important", encoding="utf-8")
        plan = cleanup.build_plan(self.root)
        self.assertFalse(any(i["src"] == "build.tmp" and i["kind"] == "queue-delete" for i in plan["items"]))

    def test_broken_symlink_destination_is_not_overwritten(self):
        (self.root / "projects/acme/notes.txt").write_text("x", encoding="utf-8")
        (self.root / "projects/acme/documents").mkdir()
        link = self.root / "projects/acme/documents/notes.txt"
        try:
            os.symlink("/nonexistent-target", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        cleanup.write_plan(self.root, cleanup.build_plan(self.root))
        cleanup.apply_plan(self.root, None)
        self.assertTrue(link.is_symlink())
        self.assertTrue((self.root / "projects/acme/notes.txt").is_file())

    def test_hook_allows_repos_bundles_and_edits_of_existing_files(self):
        ok = ["projects/acme/code/20260101-site/src/main.py",
              "projects/acme/designs/20260101-brand-kit/logo.png"]
        for rel in ok:
            self.assertEqual(run_hook({"file_path": str(self.root / rel)}, self.root).returncode, 0, rel)
        old = self.root / "projects/acme/documents/old-undated.docx"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("x", encoding="utf-8")
        r = run_hook({"file_path": str(old)}, self.root, tool_name="Edit")
        self.assertEqual(r.returncode, 0)

    def test_audit_paths_use_forward_slashes(self):
        (self.root / "projects/acme/loose.md").write_text("x", encoding="utf-8")
        paths = [f.path for f in audit_mod.audit(self.root)]
        self.assertTrue(all("\\" not in p for p in paths))


if __name__ == "__main__":
    unittest.main()
