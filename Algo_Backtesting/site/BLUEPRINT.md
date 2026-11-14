# Reports website — blueprint (how it is built, and how to rebuild it from zero)

**Seedhi baat:** website = ek page (`index.html`) jo saari backtest reports ki list dikhata hai, aur har report full
screen kholta hai. Sab kuch is repo me hai; server pe sirf copy jaati hai. Website delete ho jaye to: FTP details
`.env` me daalo, `python tools/upload_site.py --force` chalao — 5 minute me waisi hi site wapas.

- Live: https://santoshkumar.infinityfree.io/ (link also in `site/site.json`; every runner reads it from there)
- Direct report link: `<site url>#report-NN` (NN = results folder number, e.g. `#report-24`)
- Host: InfinityFree free hosting, upload by FTP (since 14 Nov 2026; before that Claude Artifacts, which kept failing
  with "Couldn't load this artifact" — those were deleted on 14 Nov 2026, do not go back to them)

## 1. The pieces (all in git except `.env`)

| Piece | Path (under `Algo_Backtesting/`) | Job |
| --- | --- | --- |
| Reports | `results/NN_<name>/report.html` | One self-contained HTML dashboard per backtest (the source of truth) |
| Report list info | `site/reports.json` | Per folder: name, type, period, one-line Hinglish result, net, pf, trades, verdict |
| Page template | `src/backtest/assets/site_template.html` | The list + viewer page; `__DATA__` is replaced with the report data |
| Builder | `tools/build_site.py` | `collect()` reads results + reports.json, `render(data)` fills the template |
| Uploader | `tools/upload_site.py` | Builds, gzips every report, uploads only changed files by FTP |
| Site link | `site/site.json` | `url` = the public address (used in the page and in Telegram messages) |
| FTP login | `.env` (git-ignored) | `SITE_PROTOCOL`, `SITE_FTP_HOST`, `SITE_FTP_PORT`, `SITE_FTP_USER`, `SITE_FTP_PASS`, `SITE_FTP_DIR`, `SITE_URL` (`.env.example` lists the keys) |
| Local copy | `site/index.html`, `site/files.json` | Written by build_site.py (plain, ungzipped page; not what the server gets) |

## 2. What is on the server (`SITE_FTP_DIR` = `/htdocs`)

```
/htdocs/index.html                       list + viewer page, "site" = SITE_URL
/htdocs/reports/<NN_name>.html.gz        each report, gzip -9 (85 MB of reports -> 27 MB)
/htdocs/reports/<NN_name>.html.gz.p1..   only when a gzipped report is still > 9.5 MB (host refuses files > 10 MB)
/htdocs/_manifest.json                   sha256 per uploaded file -> next run uploads only what changed,
                                         and removes files of reports that no longer exist (only its own files)
```
`index2.html` and the readme file in `/htdocs` are InfinityFree's own; leave them. Uploads go to `<name>.uploading`
first, the size is checked, then renamed, so visitors never get a half-written file.

## 3. How the page works (`site_template.html`)

- Data: `const D = {"reports":[...], "site":"<url>", "generated":"<time>"}`. Each report row:
  `num, folder, name, type, period, result, net, pf, trades, verdict (profit/loss/study), made, updated, tested, mb,
  href, hash ("report-NN")`, plus on the server `gz: true` and, if split, `parts: [...]`.
- List view: tally chips (count / profit / loss / study), search box, Type / Result / Sort selects, "Filter hatao"
  reset, one row per report (number, name, result line, verdict + type + period chips, Net P&L in Rs L/Cr, PF, trades,
  Generate / Last Update dates, "Get Report" button). Filters are remembered in localStorage (`algo-site-filters`).
- "Get Report" opens `<site url>#report-NN` in a new tab. On load, `route()` reads the hash and shows the viewer:
  a top bar ("Saari reports" back link, report name, "Backtested <date>" chip, load status) and an iframe.
- `loadReport(r)`: fetches `r.parts` (or `r.href`) as bytes, joins them, and if they start with `1f 8b` unzips them
  in the browser (`DecompressionStream("gzip")`), then puts the HTML into the iframe (`srcdoc`). If the server sent its
  JavaScript bot-check page instead of the gzip file, the page reloads once by itself.
- Dark/light follows the system (`prefers-color-scheme`), navy colours, mobile layout under 860 px.
- The server page gets a small head added by upload_site.py (`<!doctype html>`, charset, viewport, `body{margin:0}`),
  because the template itself starts at `<title>`.

## 4. Every new report (normal use)

1. Runner writes `results/NN_<name>/report.html` (folder number from `backtest.config.result_dir`).
2. Add / update its entry in `site/reports.json`.
3. In `Algo_Backtesting`: `.venv/Scripts/python.exe tools/upload_site.py` (`--dry-run` shows the plan first).
   It always re-uploads `index.html` (30 KB, its "generated" time changes) plus the new / changed report.
4. Telegram finish message: report HTML file + site link + `<site url>#report-NN`.

## 5. Rebuild from zero (website / account deleted)

1. Hosting: log in at infinityfree.com (or make a new free account) -> create a hosting account with a free
   subdomain (e.g. `santoshkumar.infinityfree.io`). New subdomains can take a while before they open everywhere (see 7).
2. Control panel -> "FTP Details": hostname (`ftpupload.net`), port 21, username (`if0_...`), password.
   Web folder is `/htdocs`.
3. Fill `Algo_Backtesting/.env` (keys from `.env.example`): `SITE_PROTOCOL=ftp`, host, port, user, password,
   `SITE_FTP_DIR=/htdocs`, `SITE_URL=https://<subdomain>` (no trailing slash). Never commit `.env`, never print the password.
4. If the address changed: put it in `site/site.json` (`url`, with trailing `/`) and in the "Reports website" part of
   `/CLAUDE.md`.
5. `.venv/Scripts/python.exe tools/upload_site.py --force` (about 4 minutes for 25 files / 27 MB).
6. Check in a browser (section 8). Optional: Control panel -> "Directory Privacy" on `htdocs` to put a password on it.
7. Send the (new) link on Telegram.

Repo lost too: `git clone https://github.com/santoshkrsah/Algo`, make `Algo_Backtesting/.venv`
(`pip install -r requirements.txt`), recreate `.env` (Telegram + FTP keys), then step 5. The reports themselves are
in git (`results/*/report.html`), so nothing has to be rerun.

Other host (any static web hosting works): upload the same files (`index.html` + `reports/` + manifest). Needs only:
static files over HTTPS, `.gz` served as plain bytes (no `Content-Encoding` header; if a host adds it, upload plain
`.html` instead and drop `gz`), and a per-file size limit set in `PART` in upload_site.py.

## 6. Settings that matter in `upload_site.py`

| Setting | Value | Why |
| --- | --- | --- |
| `PART` | 9,500,000 bytes | InfinityFree refuses files over 10 MB, also through FTP |
| gzip | level 9, `mtime=0` | Same input -> same bytes -> same sha256, so unchanged reports are not re-uploaded |
| Retries | 5 per file, reconnect each time | The free FTP drops connections now and then (e.g. a bad PASV reply) |
| Protocol | plain FTP (`ftps` also supported) | InfinityFree logs in with TLS but refuses `PROT P`, so data must go plain |

## 7. Known traps (seen on 14 Nov 2026)

- **JavaScript cookie check:** InfinityFree answers the first request with a small page that runs `/aes.js`, sets a
  `__test` cookie and reloads. Browsers pass it by themselves; `curl` / Python only see that page. To check from a
  script, compute the cookie: AES-128-CBC decrypt of `c` with key `a`, IV `b` (the three hex strings in that page),
  no padding, as lowercase hex; then send `Cookie: __test=<hex>`. On Windows .NET can do it:
  `[Security.Cryptography.Aes]::Create()` with `Mode='CBC'`, `Padding='None'`, `CreateDecryptor().TransformFinalBlock(...)`.
- **DNS of a new subdomain:** this PC's DNS gave a parking address (199.59.243.225) for some hours while Google /
  Cloudflare DNS already gave the real one (185.27.134.34). If the site does not open on one device: try mobile data,
  wait, or set DNS to 8.8.8.8.
- **Limits:** 10 MB per file, ~50,000 hits per day (over that = site offline 24 h), 5 GB space, unused accounts get
  deleted — open the site now and then.
- **Plain FTP:** the password travels unencrypted; use a password that is used nowhere else and change it if it was
  ever pasted somewhere (it was pasted in a Claude chat on 14 Nov 2026 -> change it in the panel, then in `.env`).

## 8. Check after every rebuild

- `tools/upload_site.py --dry-run` right after an upload says `upload 1` (only index.html) and `remove 0`.
- Browser: the list shows every report; the newest is on top; filters work.
- Open the biggest report (today 24, 4.5 MB gzipped) and one small one with "Get Report": both show their tabs and charts.
- Phone: list readable, no sideways scroll, report opens.
