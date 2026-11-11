import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import draft_writer
import workflowctl

TEMPLATE = '''---
type: ai-knowledge
status: pending
project: ""
created: "{{date}}"
source: ""
tags:
  - ai-draft
---

# {{title}}

''' + "\n\n".join("## " + h for h in draft_writer.HEADINGS) + "\n"
BODY = "\n\n".join("## " + h + "\n\n已人工筛选的测试资料。" for h in draft_writer.HEADINGS)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = workflowctl.load(ROOT / "workflow.json")
        self.task = workflowctl.load(ROOT / "tasks/demo.json")

    def result(self, **fields):
        self.task.update(fields)
        return workflowctl.route(self.task, self.config)

    def test_low_risk_uses_t3(self):
        self.assertEqual(self.result()["tier"], "T3")

    def test_cross_module_uses_t2(self):
        self.assertEqual(self.result(modules=2)["tier"], "T2")

    def test_high_risk_uses_t1_and_holds(self):
        r = self.result(risk_tags=["security"])
        self.assertEqual(r["tier"], "T1")
        self.assertTrue(r["holds"])

    def test_governance_path_cannot_use_t3(self):
        self.assertEqual(self.result(paths=["AGENTS.md"])["tier"], "T1")

    def test_failed_attempt_upgrades(self):
        self.assertEqual(self.result(attempts=1)["tier"], "T2")

    def test_three_attempts_stop(self):
        self.assertTrue(self.result(attempts=3)["holds"])

    def test_unclear_requirements_hold(self):
        self.assertTrue(self.result(requirements_clear=False)["holds"])

    def test_reviewer_defaults_t2(self):
        self.assertEqual(self.result(role="Reviewer")["tier"], "T2")

    def test_knowledge_stays_deepseek(self):
        self.assertEqual(self.result(role="Knowledge", attempts=2)["profile"], "deepseek-api")

    def test_knowledge_sensitive_input_holds(self):
        self.assertTrue(self.result(role="Knowledge", risk_tags=["credentials"])["holds"])

    def test_dispatcher_control_only(self):
        self.assertTrue(self.result(role="Dispatcher")["control_only"])

    def test_reject_unknown_role(self):
        with self.assertRaises(ValueError):
            self.result(role="RootAdmin")

    def test_reject_string_boolean(self):
        with self.assertRaises(ValueError):
            self.result(requirements_clear="true")

    def test_unknown_risk_label_holds(self):
        self.assertTrue(self.result(risk_tags=["securty"])["holds"])

    def test_zero_modules_rejected(self):
        with self.assertRaises(ValueError):
            self.result(modules=0)

    def test_profile_replacement_does_not_change_role(self):
        before = self.result()
        self.config["tiers"]["T3"] = "replacement"
        after = self.result()
        self.assertEqual(before["role"], after["role"])
        self.assertEqual(after["profile"], "replacement")

    def test_fishmuse_disabled(self):
        with self.assertRaisesRegex(ValueError, "未批准"):
            workflowctl.prepare(self.task, self.config, workflowctl.load(ROOT / "projects/fishmuse.json"), "/tmp", "/tmp/unused-packet")

    def test_prepare_preview_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            out = Path(tmp) / "packet"
            workflowctl.prepare(self.task, self.config, {"workflow_approved": True}, repo, out)
            command = (out / "command.txt").read_text()
            self.assertIn("未授权", command)
            self.assertIn("--standalone", command)
            with self.assertRaises(FileExistsError):
                workflowctl.prepare(self.task, self.config, {"workflow_approved": True}, repo, out)

    def test_codex_preview_enforces_sandbox(self):
        c = workflowctl.preview_command(self.config["profiles"]["codex-chatgpt"], "Reviewer", Path("/tmp/repo"), Path("/tmp/prompt"), Path("/tmp/settings"))
        self.assertIn("--sandbox read-only", c)
        self.assertNotIn("dangerously", c)


class DraftWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "Engineering"
        (self.vault / "90-Templates").mkdir(parents=True)
        (self.vault / "90-Templates/AI-Knowledge-Draft.md").write_text(TEMPLATE)

    def write(self, **fields):
        args = dict(vault=self.vault, body=BODY, title="验证草稿", project="Sandbox", source="repo@abc123:docs/spec.md", slug="test-draft")
        args.update(fields)
        return draft_writer.write_draft(**args)

    def test_pending_new_file_only(self):
        p = self.write()
        text = p.read_text()
        self.assertIn("status: pending", text)
        self.assertIn('project: "Sandbox"', text)
        self.assertNotIn("{{", text)
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertEqual(p.parent, self.vault / "00-Inbox/AI-Drafts")

    def test_duplicate_rejected(self):
        self.write()
        with self.assertRaisesRegex(ValueError, "相同"):
            self.write(slug="other-title")

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.write(slug="../../accepted")

    def test_approved_status_rejected(self):
        with self.assertRaises(ValueError):
            self.write(body=BODY + "\nstatus: accepted\n")

    def test_frontmatter_injection_rejected(self):
        with self.assertRaises(ValueError):
            self.write(body="---\n" + BODY)

    def test_missing_evidence_rejected(self):
        with self.assertRaises(ValueError):
            self.write(source="")

    def test_incomplete_body_rejected(self):
        with self.assertRaises(ValueError):
            self.write(body="只有一句话")

    def test_secret_pattern_rejected(self):
        with self.assertRaises(ValueError):
            self.write(body=BODY + "\nsk-" + "a" * 32)

    def test_secret_in_title_rejected(self):
        with self.assertRaises(ValueError):
            self.write(title="sk-" + "a" * 32)

    def test_symlink_target_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.vault / "00-Inbox").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.write()
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_accepted_note_not_touched(self):
        accepted = self.vault / "accepted.md"
        accepted.write_text("status: accepted\n保留原样\n")
        before = accepted.read_bytes()
        self.write()
        self.assertEqual(accepted.read_bytes(), before)

    def test_reject_unexpected_template(self):
        (self.vault / "90-Templates/AI-Knowledge-Draft.md").write_text(TEMPLATE.replace("status: pending", "status: accepted"))
        with self.assertRaises(ValueError):
            self.write()


if __name__ == "__main__":
    unittest.main()
