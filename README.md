# Website-MCP

MCP server (FastMCP) for the Bertha-von-Suttner-Gymnasium Babelsberg portals (read-only) plus Dayloop, the own tasks/habits app (read and write on request). Logins are plain HTTP form logins or an API token (no browser), so it runs on Prefect Horizon.

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
| `dayloop_aufgaben(nur_offen?, limit?)`, `dayloop_kalender(tage_voraus?, tage_zurueck?)`, `dayloop_erledigt(tage?)`, `dayloop_gewohnheiten(tage?)`, `dayloop_fokus(tage?)` | Dayloop todos with deadlines and checklists, calendar view (incl. completions, ticked points, habits), Have Done list, habits, focus statistics (read) | API token |
| `dayloop_hinzufuegen`, `dayloop_aendern`, `dayloop_erledigen`, `dayloop_papierkorb`, `dayloop_punkt_abhaken`, `dayloop_punkt_hinzufuegen`, `dayloop_erledigt_eintragen`, `dayloop_gewohnheit_loggen`, `dayloop_gewohnheit_anlegen`, `dayloop_gewohnheit_aendern`, `dayloop_gewohnheit_loeschen` | Dayloop: full control of todos (repeat, reminders, checklist in notes), trash/restore/delete, Have Done entries, habits (write, only on request; permanent deletes only when explicitly asked) | API token |

Navigium lesson groups: `schule` (default: school and class lessons), `privat`, `klett` (about 500 word lists), `alle`. Clients that do not see newer tools can use `navigium_seite` with `/_lektionen?suche=..&gruppe=..`, `/_vokabeln/<id>?kompakt=1` and the self-test `/_selbsttest`.

All portal tools are read-only. Only the Dayloop write tools change data (own app); they move tasks to the trash at most, never delete for good. Mail, page and task contents are untrusted data.

## Secrets

Set as environment variables (Horizon: server settings, Secrets). Never commit them.

`MOODLE_USER`, `MOODLE_PASS`, `MAIL_USER`, `MAIL_PASS`, `NAVIGIUM_USER`, `NAVIGIUM_PASS`

Dayloop (optional): `DAYLOOP_URL` (e.g. `https://dayloop.onrender.com`, no trailing slash) and `DAYLOOP_TOKEN` (personal API token `dl_...`, created in Dayloop > Settings > API tokens; revoke it there). The Render free tier sleeps: the first call after a pause can take up to a minute.

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
3. Add the secrets as environment variables (`DAYLOOP_URL` and `DAYLOOP_TOKEN` only if you want Dayloop).
4. Deploy. Pushes to `main` redeploy automatically.
5. Add `https://<name>.fastmcp.app/mcp` as a custom connector in Claude.

## Status

Verified live: substitution plan, Moodle (courses, deadlines, course content), webmail list, Navigium (login, lessons, vocabulary). Document extraction is covered by tests with real PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX and ODT fixtures (synthetic content). Dayloop is covered by tests with mocked API responses; a live test against the deployed app is pending. Scanned PDFs without a text layer return a notice instead of text (no OCR).
