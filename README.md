# Website-MCP

MCP server (FastMCP) with read-only access to the portals of the Bertha-von-Suttner-Gymnasium Babelsberg. Logins are plain HTTP form logins (no browser), so it runs on Prefect Horizon.

## Tools

| Tool | Site | Login |
|---|---|---|
| `vertretungsplan_lesen(klasse?)` | Substitution plan (Untis) | no |
| `moodle_kurse`, `moodle_fristen(tage)`, `moodle_kurs_inhalt(kurs_id)`, `moodle_seite(pfad)` | Moodle | yes |
| `mail_liste`, `mail_suche`, `mail_lesen` | Webmail (Roundcube) | yes |
| `navigium_uebersicht`, `navigium_seite(pfad)` | Navigium Online | yes |

All tools are read-only. Mail and page contents are untrusted data.

## Secrets

Set as environment variables (Horizon: server settings, Secrets). Never commit them.

`MOODLE_USER`, `MOODLE_PASS`, `MAIL_USER`, `MAIL_PASS`, `NAVIGIUM_USER`, `NAVIGIUM_PASS`

For local runs copy `.env.example` to `.env` (gitignored).

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/fastmcp inspect server.py:mcp
.venv/bin/python server.py   # stdio
```

Network test of the public plan: `LIVE=1 .venv/bin/python -m unittest discover -s tests -t .`

## Deploy on Prefect Horizon

1. Sign in at https://horizon.prefect.io with GitHub and select this repo.
2. Entrypoint `server.py:mcp`, enable authentication (OAuth).
3. Add the six secrets as environment variables.
4. Deploy. Pushes to `main` redeploy automatically.
5. Add `https://<name>.fastmcp.app/mcp` as a custom connector in Claude.

## Status

Vertretungsplan is verified against the live site. Moodle, webmail and Navigium logins and parsers follow the sites' public login flows but need a first run with real credentials; adjust the parsers if a site's markup differs.
