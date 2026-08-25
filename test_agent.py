import json
import tempfile
import unittest
from pathlib import Path

import agent


class AgentTests(unittest.TestCase):
    def test_finds_cross_document_fire_rating_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "drawing.txt").write_text("Mechanical rooms shall carry a 90-minute fire rating.", encoding="utf-8")
            (root / "schedule.txt").write_text("| Mark | Location | Fire Rating |\n|---|---|---|\n| D-101 | Mechanical 101 | 60 min |", encoding="utf-8")
            docs = agent.read_documents(root)
            findings = agent.deterministic_errors(docs)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["category"], "cross-document-conflict")
            self.assertEqual(findings[0]["document"], "schedule.txt")

    def test_output_is_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.txt").write_text("width: 36 in", encoding="utf-8")
            (root / "b.txt").write_text("width: 42 in", encoding="utf-8")
            docs = agent.read_documents(root)
            output = root / "out.json"
            output.write_text(json.dumps({"errors": agent.deterministic_errors(docs)}), encoding="utf-8")
            parsed = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("errors", parsed)
            self.assertEqual(len(parsed["errors"]), 0)

    def test_finds_plain_text_same_mark_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "spec.txt").write_text("P-101 pipe shall be 2 in minimum.", encoding="utf-8")
            (root / "drawing.txt").write_text("P-101 pipe shown as 1.5 in.", encoding="utf-8")
            docs = agent.read_documents(root)
            findings = agent.anchored_numeric_errors(docs)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["document"], "drawing.txt")
            self.assertIn("2 in", findings[0]["description"])


if __name__ == "__main__":
    unittest.main()
