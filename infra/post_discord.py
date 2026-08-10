"""Post stdin to the Discord webhook (Keychain service `brain`, key
DISCORD_WEBHOOK_URL), chunked to Discord's 2000-char limit. No-op if unset."""
import sys
import keyring
import httpx

try:
    url = keyring.get_password("brain", "DISCORD_WEBHOOK_URL")
except Exception as e:  # locked login keychain under launchd (-25320) → skip, don't crash
    print(f"keychain unreadable ({e}); skipping", file=sys.stderr)
    sys.exit(0)
if not url:
    print("no DISCORD_WEBHOOK_URL; skipping", file=sys.stderr)
    sys.exit(0)
text = sys.stdin.read().strip()
if not text:
    sys.exit(0)

chunks: list[str] = []
cur = ""
for line in text.splitlines(keepends=True):
    if len(cur) + len(line) > 1900 and cur:
        chunks.append(cur)
        cur = ""
    cur += line
if cur:
    chunks.append(cur)

with httpx.Client(timeout=15.0) as c:
    for ch in chunks[:6]:  # cap at 6 messages to avoid spamming
        try:
            c.post(url, json={"content": ch})
        except Exception as e:  # noqa: BLE001
            print(f"post failed: {e}", file=sys.stderr)
print(f"posted {min(len(chunks), 6)} message(s)")
