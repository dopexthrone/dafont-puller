#!/usr/bin/env python3
"""Index DaFont themes and collect permitted archives in a local library."""
import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from html import unescape
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit, parse_qs
from urllib.robotparser import RobotFileParser
from zipfile import BadZipFile, ZipFile

import requests

SITE = "https://www.dafont.com/"
DL = "https://dl.dafont.com/"
AGENT = "dafont-puller/1.0"
HEADERS = {"User-Agent": AGENT, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.8"}
EXCLUDED = {"Dingbats", "Holiday"}


def get(url):
    for attempt in range(3):
        response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=False)
        if response.status_code in (429, 403):
            raise RuntimeError(f"Host paused access ({response.status_code}). Retry later.")
        if response.status_code >= 500 and attempt < 2:
            time.sleep(2 ** (attempt + 1))
            continue
        response.raise_for_status()
        if response.status_code != 200 or not response.content:
            raise RuntimeError(f"Empty or redirected response from {url}")
        return response


def rules(host):
    policy = RobotFileParser()
    policy.parse(get(host + "robots.txt").text.splitlines())
    return policy


def html(url, policy):
    if not policy.can_fetch(AGENT, url):
        raise RuntimeError(f"Site rules disallow {url}")
    return get(url).content.decode("cp1252", errors="replace")


def clean(raw):
    return unescape(re.sub("<[^>]+>", "", raw)).strip()


def themes(body):
    group = None
    result = {}
    for href, title in re.findall(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', body, re.S|re.I):
        url = urlsplit(urljoin(SITE, unescape(href)))
        if url.netloc != "www.dafont.com":
            continue
        if url.path == "/mtheme.php":
            group = clean(title)
        elif url.path == "/bitmap.php":
            result["bitmap"] = "Bitmap"
        elif url.path == "/theme.php" and group and group not in EXCLUDED:
            cat = parse_qs(url.query).get("cat", [""])[0]
            if cat.isdecimal():
                result[cat] = group
    if not result:
        raise RuntimeError("Theme directory format unrecognized")
    return result


def rows(body):
    parts = re.split(r'<div class="lv1left\b[^"]*">', body)[1:]
    if not parts:
        raise RuntimeError("No font rows found. Page left pending.")
    seen = []
    for part in parts:
        title = re.search(r'<a href="([^"]*\.font[^"]*)"[^>]*><strong>(.*?)</strong>', part, re.S)
        slug = re.search(r'href="(?:https?:)?//dl\.dafont\.com/dl/\?f=([A-Za-z0-9_]{1,120})"', part)
        terms = re.search(r'<a[^>]*href="[^"]*#copyright"[^>]*>(.*?)</a>', part, re.S)
        if not slug or not title:
            raise RuntimeError("Incomplete font row. Page left pending.")
        seen.append((slug[1], clean(title[2]), urljoin(SITE, unescape(title[1])), clean(terms[1]) if terms else "Unknown"))
    return seen


def scan(db, limit, selected):
    policy = rules(SITE)
    groups = themes(html(SITE+"themes.php", policy))
    if selected:
        if selected not in groups:
            raise ValueError("Unknown or excluded theme")
        groups = {selected: groups[selected]}
    used = 0
    while True:
        advanced = False
        for theme, group in groups.items():
            row = db.execute("SELECT next FROM pages WHERE theme=?", (theme,)).fetchone()
            page = row[0] if row else 1
            if not page:
                continue
            if limit and used >= limit:
                return
            path = "bitmap.php?" if theme == "bitmap" else f"theme.php?cat={theme}&"
            body = html(f"{SITE}{path}page={page}&fpp=200", policy)
            fonts = rows(body)
            next_link = re.search(r'title="Keyboard shortcut: Right arrow" href="([^"]+)"',body)
            following = 0
            if next_link:
                query = parse_qs(urlsplit(unescape(next_link[1])).query)
                following = int(query["page"][0])
                if following != page + 1:
                    raise RuntimeError("Unexpected pagination")
            with db:
                db.executemany("INSERT OR REPLACE INTO fonts VALUES(?,?,?,?,?,?)",
                               [(*font,theme,group) for font in fonts])
                db.execute("INSERT OR REPLACE INTO pages VALUES(?,?)",(theme,following))
            print(f"{group} {theme}, page {page}: {len(fonts)} entries", flush=True)
            used += 1
            advanced = True
            time.sleep(1)
        if not advanced:
            break


def inspect_zip(path):
    with ZipFile(path) as archive:
        if sum(m.file_size for m in archive.infolist()) > 256*1024*1024:
            raise ValueError("Oversize archive")
        if archive.testzip() or not any(n.lower().endswith((".ttf",".otf",".woff",".woff2")) for n in archive.namelist()):
            raise ValueError("Invalid font archive")


def pull(db, out, workers):
    records = db.execute("SELECT slug FROM fonts ORDER BY slug").fetchall()
    if not records:
        raise ValueError("Index at least one page first")
    policy = rules(DL)
    def retrieve(record):
        slug = record[0]
        if not re.fullmatch("[A-Za-z0-9_]{1,120}",slug):
            raise ValueError("Invalid catalogue identifier")
        path = out / (slug+".zip")
        if path.exists():
            try:
                inspect_zip(path)
                return
            except (ValueError, OSError, BadZipFile):
                pass
        url = DL + "dl/?f=" + slug
        if not policy.can_fetch(AGENT, url):
            raise RuntimeError("DaFont's download host disallows automated archive retrieval. Catalogue saved. Use individual font source links for manual downloads.")
        response = get(url)
        with NamedTemporaryFile(dir=out, delete=False) as file:
            temp = Path(file.name)
        try:
            temp.write_bytes(response.content)
            inspect_zip(temp)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Bound outstanding requests and stop scheduling after a failure.
        for i in range(0,len(records),workers):
            list(pool.map(retrieve,records[i:i+workers]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["index","pull","status"])
    parser.add_argument("--output", type=Path, default=Path.home()/"DaFont")
    parser.add_argument("--theme", help="numeric theme ID or bitmap")
    parser.add_argument("--pages",type=int,help="maximum listing pages this run")
    parser.add_argument("--workers",type=int,choices=range(1,5),default=4)
    args = parser.parse_args()
    if args.pages is not None and args.pages < 1:
        parser.error("--pages must be positive")
    args.output.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(args.output/"catalog.sqlite")) as db:
        db.executescript("CREATE TABLE IF NOT EXISTS fonts(slug TEXT PRIMARY KEY,name TEXT,source TEXT,label TEXT,theme TEXT,category TEXT); CREATE TABLE IF NOT EXISTS pages(theme TEXT PRIMARY KEY,next INTEGER);")
        if args.command == "index":
            scan(db,args.pages,args.theme)
        elif args.command == "pull":
            pull(db,args.output,args.workers)
        count = db.execute("SELECT count(*) FROM fonts").fetchone()[0]
        finished = db.execute("SELECT count(*) FROM pages WHERE next=0").fetchone()[0]
        print(f"{count} indexed, {finished} themes complete, {sum(1 for _ in args.output.glob('*.zip'))} local ZIPs")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, requests.RequestException) as error:
        sys.exit(str(error))
