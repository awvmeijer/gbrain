"""Offline tests for the digest bridge retry + hollow-digest guard.

A local stub HTTP server stands in for the Max bridge (no network, no posting).
Run: ~/gbrain/sidecars/.venv/bin/python -m unittest recipes/fintwit-analyst/test_digest_guard.py
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import digest  # noqa: E402

GOOD = "## Theme 1\n" + "NVDA bullish per @source, level 180, catalyst earnings. " * 5


def stub_bridge(responses: list[tuple[int, str]]):
    """Serve the given (status, content) pairs in order, then repeat the last."""
    calls = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length", 0)))
            status, content = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            body = (json.dumps({"choices": [{"message": {"content": content}}]})
                    if status == 200 else "Service Unavailable").encode()
            self.send_response(status)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}", calls


class BridgeRetry(unittest.TestCase):
    def run_with(self, responses):
        srv, url, calls = stub_bridge(responses)
        slept = []
        try:
            out, err = digest.synthesize(url, "m", "prompt", backoff=(1, 2), sleep=slept.append)
        finally:
            srv.shutdown()
        return out, err, calls["n"], slept

    def test_503_then_success_retries(self):
        out, err, n, slept = self.run_with([(503, ""), (200, GOOD)])
        self.assertEqual(out, GOOD.strip())
        self.assertEqual((n, slept), (2, [1]))

    def test_persistent_503_gives_up_with_reason(self):
        out, err, n, slept = self.run_with([(503, "")])
        self.assertEqual(out, "")
        self.assertIn("503", err)
        self.assertEqual((n, slept), (3, [1, 2]))

    def test_hollow_output_is_retried_then_refused(self):
        out, err, n, _ = self.run_with([(200, "_Nothing to report._")])
        self.assertEqual(out, "")
        self.assertIn("hollow", err)
        self.assertEqual(n, 3)

    def test_4xx_not_retried(self):
        out, err, n, _ = self.run_with([(400, "")])
        self.assertEqual((out, n), ("", 1))


class Hollow(unittest.TestCase):
    def test_reasons(self):
        self.assertTrue(digest.hollow_reason(""))
        self.assertTrue(digest.hollow_reason("_Nothing to report._"))      # 2026-10-14
        self.assertTrue(digest.hollow_reason("_Thin window — little to report._"))
        self.assertEqual(digest.hollow_reason(GOOD), "")


if __name__ == "__main__":
    unittest.main()
