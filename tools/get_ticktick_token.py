"""One-time helper: get a TickTick Open API access token via OAuth (runs locally, nothing is stored).

1. Register an app at https://developer.ticktick.com/manage (Manage Apps, New App).
   Set the OAuth redirect URL to  http://localhost:8765/callback
2. Run:  .venv/bin/python tools/get_ticktick_token.py
   Enter client id and client secret (the secret input is hidden).
3. Open the printed URL, log in to TickTick and allow access. The script catches the redirect.
4. Put the printed access token into Horizon as secret TICKTICK_ACCESS_TOKEN.

The token is valid for a limited time (about 6 months); rerun the script when tools report 401.
Never commit the token or paste it into chats.
"""
from __future__ import annotations

import getpass
import secrets
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

REDIRECT = "http://localhost:8765/callback"
SCOPE = "tasks:read tasks:write"
AUTHORIZE = "https://ticktick.com/oauth/authorize"
TOKEN = "https://ticktick.com/oauth/token"


class Catcher(BaseHTTPRequestHandler):
    params: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802
        Catcher.params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Fertig, du kannst dieses Fenster schliessen.".encode())

    def log_message(self, *args: object) -> None:
        return


def main() -> int:
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret (hidden): ").strip()
    state = secrets.token_urlsafe(12)
    url = AUTHORIZE + "?" + urllib.parse.urlencode(
        {"client_id": client_id, "scope": SCOPE, "state": state, "redirect_uri": REDIRECT, "response_type": "code"})
    print("\nOpen this URL in your browser and allow access:\n" + url + "\n")
    server = HTTPServer(("127.0.0.1", 8765), Catcher)
    server.handle_request()
    code, returned = Catcher.params.get("code", [""])[0], Catcher.params.get("state", [""])[0]
    if not code or returned != state:
        print("No valid authorization code received.", file=sys.stderr)
        return 1
    response = httpx.post(TOKEN, auth=(client_id, client_secret), data={
        "code": code, "grant_type": "authorization_code", "scope": SCOPE, "redirect_uri": REDIRECT})
    token = response.json().get("access_token") if response.status_code == 200 else None
    if not token:
        print(f"Token exchange failed: HTTP {response.status_code} {response.text[:200]}", file=sys.stderr)
        return 1
    print("Access token (set as TICKTICK_ACCESS_TOKEN):\n" + token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
