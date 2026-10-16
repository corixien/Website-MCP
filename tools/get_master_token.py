"""One-time helper: exchange a Google `oauth_token` cookie for a Keep master token.

1. Open https://accounts.google.com/EmbeddedSetup in a browser and sign in.
2. After the final screen, copy the cookie `oauth_token` (starts with "oauth2_4/") from the browser's
   developer tools (Application/Storage, Cookies, accounts.google.com).
3. Run:  .venv/bin/python tools/get_master_token.py you@gmail.com
4. Put the printed token into Horizon as secret GKEEP_MASTER_TOKEN (and GKEEP_EMAIL).

The master token grants access to the whole Google account: never commit it or paste it into chats.
Prefer a second Google account that only has the needed notes shared with it.
"""
from __future__ import annotations

import getpass
import secrets
import sys

import gpsoauth


def main() -> int:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print(__doc__)
        return 2
    email = sys.argv[1]
    oauth_token = getpass.getpass("oauth_token cookie (input hidden): ").strip()
    android_id = secrets.token_hex(8)
    response = gpsoauth.exchange_token(email, oauth_token, android_id)
    token = response.get("Token")
    if not token:
        print(f"Failed: {response.get('Error', 'unknown error')}", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
