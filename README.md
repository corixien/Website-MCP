# Website-MCP

MCP server (FastMCP) with read-only access to the portals of the Bertha-von-Suttner-Gymnasium Babelsberg. Logins are plain HTTP form logins (no browser), so it runs on Prefect Horizon.

## Tools

| Tool | Site | Login |
|---|---|---|
| `vertretungsplan_lesen(klasse?)` | Substitution plan (Untis) | no |
| `moodle_kurse`, `moodle_fristen(tage)`, `moodle_kurs_inhalt(kurs_id)`, `moodle_seite(pfad)` | Moodle courses, deadlines, structure, any page | yes |
| `moodle_dateien(kurs_id, abschnitt?)`, `moodle_datei_lesen(url, max_zeichen, ab)` | Moodle files: PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX, ODF, TXT, HTML | yes |
| `moodle_mitteilungen`, `moodle_nachricht_lesen(id)`, `moodle_benachrichtigungen` | Moodle messages, notifications | yes |
| `mail_liste`, `mail_suche`, `mail_lesen` | Webmail (Roundcube) | yes |
| `navigium_lektionen(suche?, gruppe?, limit?)`, `navigium_vokabeln(lektion_id, kompakt?)`, `navigium_auftraege` | Navigium vocabulary lessons (JSON API), assignments | yes |
| `navigium_uebersicht`, `navigium_seite(pfad)` | Navigium pages | yes |

Navigium lesson groups: `schule` (default: school and class lessons), `privat`, `klett` (about 500 word lists), `alle`. Clients that do not see newer tools can use `navigium_seite` with `/_lektionen?suche=..&gruppe=..`, `/_vokabeln/<id>?kompakt=1` and the self-test `/_selbsttest`.

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

Document extraction is covered by tests with real PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX and ODT fixtures (synthetic content). Logins and site parsers are checked against the live sites after each deploy. Scanned PDFs without a text layer return a notice instead of text (no OCR).
