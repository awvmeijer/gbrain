#!/usr/bin/env python3
"""Claude Code <-> BRAINS session hooks (gbrain-native). Reads the hook JSON on stdin.

  start : resolve the repo → GET /api/context → print additionalContext JSON so
          the session opens with what the brain knows about this repo.
  end   : parse the transcript (first prompt, files touched) + recent commits →
          POST /api/session-record, writing one immutable `session` page.

Fail-open: ANY error prints nothing and exits 0 — a wedged brain never blocks a
session. Run via the sidecar venv python (has `keyring`). ~2s hard timeout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

API = os.environ.get("BRAINS_API", "http://127.0.0.1:8787")


KEY_FILE = os.path.expanduser("~/.gbrain/client.key")


def _key() -> str:
    # Key file first — a Keychain read from a hook pops the macOS unlock dialog
    # on every session start/end whenever the login keychain is locked.
    try:
        with open(KEY_FILE) as f:
            k = f.read().strip()
        if k:
            return k
    except Exception:
        pass
    try:
        import keyring
        return keyring.get_password("brain", "CAPTURE_KEY") or ""
    except Exception:
        return ""


def _repo(cwd: str) -> str:
    for args in (["remote", "get-url", "origin"], ["rev-parse", "--show-toplevel"]):
        try:
            r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return ""


def _http(method: str, path: str, body: dict | None = None):
    hdr = {"X-Brain-Key": _key()}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read())


def _parse_transcript(path: str, cwd: str):
    title, files = "session", []
    try:
        first = True
        with open(path) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
                content = msg.get("content")
                if first and ev.get("type") == "user":
                    txt = content if isinstance(content, str) else (
                        " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                        if isinstance(content, list) else "")
                    if txt.strip() and not txt.strip().startswith("<"):
                        title = txt.strip().split("\n")[0][:80]
                        first = False
                if isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict) and p.get("type") == "tool_use":
                            fp = (p.get("input") or {}).get("file_path")
                            if fp and fp not in files:
                                files.append(fp)
    except Exception:
        pass
    commits = []
    try:
        r = subprocess.run(["git", "-C", cwd, "log", "--oneline", "-5"], capture_output=True, text=True, timeout=3)
        commits = [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    return title, files[:40], commits


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    try:
        inp = json.load(sys.stdin)
    except Exception:
        inp = {}
    cwd = inp.get("cwd") or os.getcwd()
    repo = _repo(cwd)
    if not repo:
        return  # not a git repo → no-op (intentional)
    try:
        if mode == "start":
            ctx = _http("GET", "/api/context?repo=" + urllib.parse.quote(repo)).get("context", "")
            if ctx:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
        else:
            title, files, commits = _parse_transcript(inp.get("transcript_path", ""), cwd)
            _http("POST", "/api/session-record", {"repo": repo, "title": title, "files": files,
                                                  "commits": commits, "session_id": inp.get("session_id", "")})
    except Exception:
        return  # fail-open


if __name__ == "__main__":
    main()
