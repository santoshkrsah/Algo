"""Reports website on our own web hosting (InfinityFree): build the site page and upload it by FTP.

    python tools/upload_site.py             # build + upload only what changed
    python tools/upload_site.py --dry-run   # show what would be uploaded / removed, upload nothing
    python tools/upload_site.py --force     # upload everything again

Settings come from Algo_Backtesting/.env (git-ignored): SITE_PROTOCOL (ftp / ftps), SITE_FTP_HOST, SITE_FTP_PORT,
SITE_FTP_USER, SITE_FTP_PASS, SITE_FTP_DIR (folder the website serves, e.g. /htdocs), SITE_URL (its address).

What goes up (SITE_FTP_DIR/...):
    index.html                       the same page as tools/build_site.py (list + full-screen viewer), "site" = SITE_URL
    reports/<NN_name>.html.gz        every results/NN_*/report.html, gzip-compressed (the page unzips it in the browser)
    reports/<NN_name>.html.gz.pN     only if the compressed report is still over PART bytes: the host refuses files
                                     over 10 MB, so it is cut into parts that the page joins again
    _manifest.json                   sha256 of every uploaded file: the next run uploads only files whose hash changed
                                     and removes files of reports that no longer exist (only files it uploaded itself)
Files are uploaded under a temporary name and renamed when complete, so visitors never see a half-written file.
"""
import argparse
import ftplib
import gzip
import hashlib
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_site  # noqa: E402

PART = 9_500_000            # bytes per uploaded file (host limit: 10 MB per file)
MANIFEST = "_manifest.json"
HEAD = ('<!doctype html><html lang="hi"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><style>body{margin:0}</style>\n')


def settings() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    need = ["SITE_FTP_HOST", "SITE_FTP_USER", "SITE_FTP_PASS", "SITE_FTP_DIR", "SITE_URL"]
    if not all(env.get(k) for k in need):
        sys.exit("Algo_Backtesting/.env me ye bharo: " + ", ".join(k for k in need if not env.get(k)))
    return env


def build(site_url: str) -> dict[str, bytes]:
    """{path inside SITE_FTP_DIR: bytes} for the whole site."""
    data, files, missing = build_site.collect()
    data["site"] = site_url.rstrip("/") + "/"
    out = {}
    for r in data["reports"]:
        z = gzip.compress((build_site.ROOT / files[r["href"]]).read_bytes(), compresslevel=9, mtime=0)
        name = f"reports/{r['folder']}.html.gz"
        r["gz"], r["mb"] = True, round(len(z) / 1e6, 1)
        if len(z) <= PART:
            r["href"] = name
            out[name] = z
        else:
            r["parts"] = [f"{name}.p{i + 1}" for i in range((len(z) + PART - 1) // PART)]
            for i, p in enumerate(r["parts"]):
                out[p] = z[i * PART:(i + 1) * PART]
            r["href"] = r["parts"][0]
    out["index.html"] = (HEAD + build_site.render(data) + "\n</html>\n").encode("utf-8")
    if missing:
        print("site/reports.json me entry nahi hai (naam/type/result khaali dikhega):", ", ".join(missing))
    return out


class Remote:
    """FTP session that reconnects and retries (the free host drops connections now and then)."""

    def __init__(self, env: dict):
        self.env, self.ftp = env, None

    def connect(self):
        e = self.env
        tls = e.get("SITE_PROTOCOL", "ftp").lower() == "ftps"
        f = ftplib.FTP_TLS() if tls else ftplib.FTP()
        f.connect(e["SITE_FTP_HOST"], int(e.get("SITE_FTP_PORT") or 21), timeout=120)
        f.login(e["SITE_FTP_USER"], e["SITE_FTP_PASS"])
        if tls:
            f.prot_p()
        f.set_pasv(True)
        f.cwd(e["SITE_FTP_DIR"])
        self.ftp = f

    def run(self, what: str, fn, tries: int = 5):
        for i in range(tries):
            try:
                if self.ftp is None:
                    self.connect()
                return fn(self.ftp)
            except ftplib.error_perm:
                raise
            except Exception as ex:  # dropped connection, bad passive reply, timeout ...
                print(f"  {what}: {type(ex).__name__} {str(ex)[:80]} - retry {i + 1}/{tries - 1}", flush=True)
                try:
                    self.ftp.close()
                except Exception:
                    pass
                self.ftp = None
                time.sleep(3 * (i + 1))
        raise RuntimeError(f"{what}: gave up after {tries} tries")

    def read(self, path: str) -> bytes | None:
        buf = io.BytesIO()
        try:
            self.run(f"read {path}", lambda f: f.retrbinary(f"RETR {path}", buf.write))
        except ftplib.error_perm:
            return None
        return buf.getvalue()

    def put(self, path: str, blob: bytes):
        tmp = path + ".uploading"

        def go(f):
            if "/" in path:
                try:
                    f.mkd(path.rsplit("/", 1)[0])
                except ftplib.error_perm:
                    pass
            f.storbinary(f"STOR {tmp}", io.BytesIO(blob))
            if f.size(tmp) != len(blob):
                raise OSError(f"size on server {f.size(tmp)} != {len(blob)}")
            try:
                f.delete(path)
            except ftplib.error_perm:
                pass
            f.rename(tmp, path)
        self.run(f"upload {path}", go)

    def delete(self, path: str):
        try:
            self.run(f"delete {path}", lambda f: f.delete(path))
        except ftplib.error_perm:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    env = settings()
    site = build(env["SITE_URL"])
    new = {p: hashlib.sha256(b).hexdigest() for p, b in site.items()}
    rem = Remote(env)
    raw = rem.read(MANIFEST)
    old = json.loads(raw) if raw else {}
    todo = [p for p in site if a.force or old.get(p) != new[p]]
    todo.sort(key=lambda p: p == "index.html")          # reports first, the page that links them last
    gone = [p for p in old if p not in new]
    size = sum(len(site[p]) for p in todo)
    print(f"{len(site)} files ({sum(map(len, site.values())) / 1e6:.1f} MB on the server); upload {len(todo)} "
          f"({size / 1e6:.1f} MB), remove {len(gone)}", flush=True)
    if a.dry_run:
        for p in todo:
            print("  upload", p, f"{len(site[p]) / 1e6:.2f} MB")
        for p in gone:
            print("  remove", p)
        return 0
    t0 = time.time()
    for i, p in enumerate(todo, 1):
        rem.put(p, site[p])
        print(f"  [{i}/{len(todo)}] {p} {len(site[p]) / 1e6:.2f} MB", flush=True)
    for p in gone:
        rem.delete(p)
        print("  removed", p, flush=True)
    rem.put(MANIFEST, json.dumps(new, indent=1).encode("utf-8"))
    rem.run("quit", lambda f: f.quit())
    print(f"done in {(time.time() - t0) / 60:.1f} min -> {env['SITE_URL'].rstrip('/')}/", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
