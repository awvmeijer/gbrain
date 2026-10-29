"""Offline tests for the X self-heal path (no network).

Run: ~/gbrain/sidecars/.venv/bin/python -m unittest recipes/x-to-brain/test_selfheal.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import collect  # noqa: E402
import heal_instances  # noqa: E402

RSS = ("<?xml version='1.0'?><rss><channel>"
       "<item><title>$NVDA breakout</title><link>https://x.com/a/status/{n}</link>"
       "<pubDate>Wed, 14 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>")


class IsRss(unittest.TestCase):
    def test_validation(self):
        self.assertTrue(heal_instances.is_rss(200, "application/rss+xml", RSS)[0])
        self.assertFalse(heal_instances.is_rss(403, "text/html", "<html>")[0])
        # 200 + HTML anti-bot page was the 2026-10-12 failure mode
        self.assertFalse(heal_instances.is_rss(200, "text/html", "<!doctype html><item>")[0])
        self.assertFalse(heal_instances.is_rss(200, "application/rss+xml", "<rss></rss>")[0])


class RetryOnLowCoverage(unittest.TestCase):
    def test_reprobe_and_retry_recovers_unreachable_handles(self):
        handles = [f"h{i}" for i in range(10)]
        healed = {"n": 0}

        def fake_heal(force_status=True):
            healed["n"] += 1
            return f"heal #{healed['n']}"

        def fake_fetch(client, handle):
            # Every instance is dead until the SECOND heal (the in-run re-probe).
            return RSS.format(n=handle) if healed["n"] >= 2 else None

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(collect, "_heal", fake_heal), \
                mock.patch.object(collect, "_fetch_rss", fake_fetch), \
                mock.patch.object(collect, "_handles", lambda: handles), \
                mock.patch.object(collect, "RUN_STATE", Path(tmp) / "state.json"), \
                mock.patch.object(collect, "LOGS", Path(tmp)), \
                mock.patch.object(collect.time, "sleep", lambda s: None), \
                mock.patch.object(sys, "argv", ["collect.py", tmp, "--heal"]):
            (Path(tmp) / "state.json").write_text(json.dumps({"last_success_day": "2026-10-12"}))
            collect.main()
            state = json.loads((Path(tmp) / "state.json").read_text())
            self.assertEqual(healed["n"], 2, "low coverage must trigger exactly one re-probe")
            self.assertEqual(state["handles_ok"], 10)
            self.assertEqual(state["backfilled_days"], ["2026-10-14"])
            self.assertEqual(len(list((Path(tmp) / "x").rglob("*.md"))), 10)

    def test_same_day_rerun_merges_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            t1 = {"hm": "09:00", "text": "first", "link": "l1"}
            t2 = {"hm": "15:00", "text": "second", "link": "l2"}
            self.assertTrue(collect._write_day(out, "acct", "2026-10-15", [t1]))
            self.assertTrue(collect._write_day(out, "acct", "2026-10-15", [t2]))
            self.assertFalse(collect._write_day(out, "acct", "2026-10-15", [t2]))
            body = (out / "x" / "acct" / "2026-10-15.md").read_text()
            self.assertIn("first", body)
            self.assertIn("second", body)


class HealRewrite(unittest.TestCase):
    def test_all_dead_list_recovers_from_pool(self):
        good = "https://good.example"

        def fake_probe(client, url, handles):
            return (True, "20 items") if url == good else (False, "HTTP 403")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(heal_instances, "probe", fake_probe), \
                mock.patch.object(heal_instances, "FALLBACK", [good]):
            f = Path(tmp) / "instances.txt"
            f.write_text("dead1.example\ndead2.example\n# never: bad.example\n")
            s = heal_instances.heal(f, use_status=False, extra=["https://bad.example"])
            active, never = heal_instances.read_file(f)
            self.assertEqual(active[0], good, "working instance must move to the top")
            self.assertNotIn("https://bad.example", active, "blocklist must hold")
            self.assertEqual(never, {"bad.example"})
            self.assertEqual(s["added"], ["good.example"])
            self.assertTrue((Path(tmp) / "instances.log").read_text().strip())


if __name__ == "__main__":
    unittest.main()
