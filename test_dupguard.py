#!/usr/bin/env python3
"""Tests for dupguard — run with: python3 test_dupguard.py (zero-dependency unittest)."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dupguard import DupGuard, Verdict  # noqa: E402


class TestDupGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "dg.jsonl")

    def test_first_send_allowed(self):
        g = DupGuard(path=self.log)
        v = g.check("Nightly report")
        self.assertFalse(v.suppressed)

    def test_window_hit_suppresses(self):
        g = DupGuard(path=self.log, window=1800)
        g.log("Nightly report")
        v = g.check("Nightly report")
        self.assertTrue(v.suppressed)
        self.assertIn("window-hit", v.reason)

    def test_window_expiry_allows(self):
        g = DupGuard(path=self.log, window=0.2)
        g.log("Nightly report")
        time.sleep(0.3)
        v = g.check("Nightly report")
        self.assertFalse(v.suppressed)

    def test_different_titles_independent(self):
        g = DupGuard(path=self.log, window=1800)
        g.log("Alpha summary")
        v = g.check("Beta summary")
        self.assertFalse(v.suppressed)

    def test_force_bypasses_and_logs(self):
        g = DupGuard(path=self.log, window=1800)
        g.log("Alert")
        v = g.check("Alert", force=True)
        self.assertFalse(v.suppressed)
        self.assertTrue(v.forced)
        g.log("Alert", force=True)
        s = g.stats()
        self.assertEqual(s["forced"], 1)

    def test_emoji_and_case_normalized(self):
        g = DupGuard(path=self.log, window=1800)
        g.log("🚨 NIGHTLY   report 🚨")
        v = g.check("nightly report")
        self.assertTrue(v.suppressed, "emoji/case variants must dedupe to same key")

    def test_torn_tail_line_tolerated(self):
        g = DupGuard(path=self.log, window=1800)
        g.log("Good entry")
        with open(self.log, "a", encoding="utf-8") as f:
            f.write('{"ts": 123, "key": "ab')  # simulated crash mid-write
        v = g.check("Good entry")
        self.assertTrue(v.suppressed, "torn tail must not crash or un-suppress")

    def test_stats_shape(self):
        g = DupGuard(path=self.log, window=1800)
        g.log("A"); g.log("B"); g.log("A")
        s = g.stats()
        self.assertEqual(s["records"], 3)
        self.assertEqual(s["distinct_titles"], 2)

    def test_cli_check_exit_codes(self):
        import subprocess
        r1 = subprocess.run([sys.executable, os.path.join(os.path.dirname(self.log), "..") and sys.path[0] + "/dupguard.py",
                             "--log", self.log, "check", "--title", "CLI test"],
                            capture_output=True, text=True)
        self.assertIn(r1.returncode, (0, 3))
        payload = json.loads(r1.stdout.strip().splitlines()[-1])
        self.assertIn("suppressed", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
