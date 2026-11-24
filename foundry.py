"""Collect exact listed desktop faces from author-linked upstream releases."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
from tempfile import NamedTemporaryFile
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile, ZipFile

import requests

from puller import AGENT, SITE, clean, html, rules

# (Indexed designer, indexed author link, publisher-controlled distribution)
SOURCES = [
    (
        "Typodermic Fonts",
        "typodermicfonts.com",
        "https://typodermicfonts.com/assets/downloads/typodermic-public-domain-2024-12.zip",
    ),
    (
        "Typodermic Fonts",
        "typodermicfonts.com",
        "https://typodermicfonts.com/assets/downloads/typodermic-free-fonts-2026i.zip",
    ),
    ("GGBotNet", "ggbot.net", "ggbotnet/fonts-cc0"),
    ("P. Baudin", "github.com/pbaudin/ACSF", "pbaudin/ACSF"),
    (
        "Patch",
        "github.com/patchstep",
        "https://github.com/patchstep/Pixelary/releases/download/1.1/Pixelary.otf",
    ),
    ("Rainkarnichi", "github.com/rainkarnichi", "rainkarnichi/amburegul.font"),
    ("Frédéric Bisson", "github.com/Zigazou/Minitel-Canvas", "Zigazou/Minitel-Canvas"),
    (
        "Televo",
        "github.com/Televo/kingdom-hearts-recollection",
        "Televo/kingdom-hearts-recollection",
    ),
    ("Witherbear", "witherbear.github.io", "Witherbear/actbit"),
    (
        "Gerhard Großmann",
        "charakterziffer.github.io",
        "charakterziffer/charakterziffer.github.io",
    ),
    ("AurekFonts", "aurekfonts.github.io", "AurekFonts/AurekFonts.github.io"),
]
# Downloaded from the publisher's /downloads/ page. Each ZIP has separate terms.
BUNDLES = {
    "typodermic-public-domain-2024-12.zip": "834518218043751906b9aba9d5fc30b952c8a3eaa18dca2c8fa07f7fafdf3b34",
    "typodermic-free-fonts-2026i.zip": "d2ea11ff669f1752eed96754ac91e38e6a77eddce748ebeb1e55b3d3222316ad",
}


def norm(s):
    return "".join(c for c in s.casefold() if c.isalnum())


def key(path):
    p = PurePosixPath(path)
    return norm(p.stem), p.suffix.lower()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(data)
    os.replace(f.name, path)


def gh(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint]))


PIXELARY = "https://github.com/patchstep/Pixelary/releases/download/1.1/Pixelary.otf"
PIXELARY_COMMIT = "8f9c54fad51deadd79aa0bb64cc6b7b3582fa0e8"


PIXELARY_LICENSE = "9db401e87fa47b6cb2375ff8446e6a7dbb02affe3e2f9395df0f759b8f2bc445"


def snapshot(source, cache):
    if source == PIXELARY:

        def read_release(name):
            if name == "Pixelary.otf":
                url, expected = (
                    source,
                    "e7e154b727b63489ce43222ebafa14315ce814e6e1ec713a49aacaef5058fe23",
                )
            else:
                url = f"https://raw.githubusercontent.com/patchstep/Pixelary/{PIXELARY_COMMIT}/LICENSE"
                expected = PIXELARY_LICENSE
            target = cache / "blobs" / expected
            if target.is_file() and digest(target.read_bytes()) == expected:
                return target.read_bytes()
            r = requests.get(url, timeout=40)
            r.raise_for_status()
            if digest(r.content) != expected:
                raise ValueError("Pixelary release checksum mismatch")
            atomic(target, r.content)
            return r.content

        return PIXELARY_COMMIT, ["Pixelary.otf", "LICENSE"], read_release
    if source.startswith("https:"):
        zip_path = cache / PurePosixPath(urlsplit(source).path).name
        expected = BUNDLES[zip_path.name]
        if not zip_path.exists() or digest(zip_path.read_bytes()) != expected:
            r = requests.get(
                source,
                headers={
                    "User-Agent": AGENT,
                    "Accept": "application/zip, application/octet-stream;q=0.9, */*;q=0.1",
                },
                timeout=90,
            )
            r.raise_for_status()
            if digest(r.content) != expected:
                raise ValueError("Publisher bundle changed; review its revision")
            atomic(zip_path, r.content)
        with ZipFile(zip_path) as z:
            paths = z.namelist()

        def read(path):
            with ZipFile(zip_path) as z:
                return z.read(path)

        return expected, paths, read
    state = cache / (source.replace("/", "--") + ".json")
    if not state.exists():
        commit = gh("repos/" + source + "/commits/HEAD")["sha"]
        tree = gh(f"repos/{source}/git/trees/{commit}?recursive=1")
        if tree["truncated"]:
            raise ValueError("Incomplete upstream tree")
        atomic(
            state,
            json.dumps(
                [
                    commit,
                    {
                        e["path"]: e["sha"]
                        for e in tree["tree"]
                        if e["type"] == "blob" and e["mode"] == "100644"
                    },
                ]
            ).encode(),
        )
    commit, entries = json.loads(state.read_text())

    def read(path):
        expected = entries[path]
        target = cache / "blobs" / expected
        data = target.read_bytes() if target.exists() else None

        def blob_id(b):
            return hashlib.sha1(f"blob {len(b)}\0".encode() + b).hexdigest()

        if data is None or blob_id(data) != expected:
            r = requests.get(
                f"https://raw.githubusercontent.com/{source}/{commit}/" + quote(path),
                timeout=40,
            )
            r.raise_for_status()
            data = r.content
            if blob_id(data) != expected:
                raise ValueError("Source blob checksum mismatch")
            atomic(target, data)
        return data

    return commit, list(entries), read


def inventory(root, out):
    names, post, hashes = set(), set(), set()
    collection = root / "catalog/fonts.json"
    if collection.is_file():
        for family in json.loads(collection.read_text())["families"]:
            for font in family["files"]:
                hashes.add(font.get("sha256"))
                if font.get("postscript"):
                    post.add(norm(font["postscript"]))
    for p in root.rglob("*"):
        if out in p.parents or not p.is_file():
            continue
        if p.suffix.lower() == ".zip":
            try:
                with ZipFile(p) as z:
                    names.update(
                        key(n)
                        for n in z.namelist()
                        if n.lower().endswith((".otf", ".ttf"))
                    )
            except BadZipFile:
                continue
        elif p.suffix.lower() in (".otf", ".ttf"):
            names.add(key(p.name))
    installed = subprocess.check_output(
        ["fc-list", "--format", "%{postscriptname}\n"], text=True
    )
    post.update(norm(n) for n in installed.splitlines() if n)
    return names, post, hashes


def license_for(path, paths):
    files = set(paths)
    for directory in PurePosixPath(path).parents:
        for name in (
            "LICENSE",
            "License.txt",
            "LICENSE.txt",
            "LICENSE.md",
            "OFL.txt",
            "OFL.TXT",
            "COPYING",
            "Typodermic Desktop EULA 2026.pdf",
            "license.txt",
        ):
            candidate = str(directory / name)
            if candidate in files:
                return candidate


def same_face(title, filename, family, style, ps, publisher):
    stem = PurePosixPath(filename).stem
    if publisher == "Typodermic Fonts":
        # This publisher spells out numbered editions in its OpenType names.
        # Keep the number itself while comparing the two spellings.
        def numbered(value):
            return re.sub(
                r"(?i)\b(two|nine)\b",
                lambda m: {"two": "2", "nine": "9"}[m[0].lower()],
                value,
            )

        title, stem, family, style = map(numbered, (title, stem, family, style))
    title_on_file = publisher == "Typodermic Fonts" and norm(title) == norm(stem)
    if (
        not ps
        or not norm(title)
        or (norm(title) not in norm(family + style) and not title_on_file)
    ):
        return False
    equivalents = {norm(ps), norm(family + style)}
    if style.casefold() in ("regular", "normal", "book"):
        equivalents.add(norm(family))
    if norm(stem) in equivalents:
        return True
    if publisher != "Typodermic Fonts":
        return False
    if ps.endswith("-Regular") and norm(stem) == norm(ps[:-8]):
        return True
    # This publisher abbreviates the style in the filename. Expand only
    # trailing style words after the independently identified family name.
    words = re.split(r"[\s_-]+", stem)
    codes = {
        "rg": "regular",
        "cd": "condensed",
        "ex": "expanded",
        "el": "extralight",
        "lt": "light",
        "bk": "book",
        "bd": "bold",
        "eb": "extrabold",
        "sc": "semicondensed",
        "se": "semiexpanded",
        "ul": "ultralight",
        "sb": "semibold",
        "th": "thin",
        "hl": "hairline",
        "bl": "black",
        "hv": "heavy",
        "comp": "compressed",
        "cond": "condensed",
        "it": "italic",
    }
    for i in range(1, len(words)):
        if norm(" ".join(words[:i])) != norm(family):
            continue
        suffix = [codes.get(w.casefold(), w.casefold()) for w in words[i:]]
        if norm(family) == "nk57monospace" and suffix[:1] == ["no"]:
            suffix = suffix[1:]  # The publisher's normal-width series.
        if "".join(suffix) == norm(style):
            return True
        if len(suffix) > 1:
            suffix = [part for part in suffix if part != "regular"]
        if "".join(suffix) == norm(style):
            return True
    return False


def import_face(
    db, out, listing, filename, publisher, paths, matches, read, inventory, repair
):
    slug, title, url, website, label = listing
    designer, source, rev = publisher
    names, post, hashes = inventory
    if not repair and key(filename) in names:
        return "already in library"
    if not matches:
        return "no exact upstream filename"
    if len(matches) > 1:
        return "ambiguous upstream path"
    member = matches[0]
    if source == "Witherbear/actbit":
        return "no font-specific terms"  # Repository licence covers the application.
    lic = license_for(member, paths)
    if source == "Televo/kingdom-hearts-recollection" and "README.md" in paths:
        lic = "README.md"  # The publisher explicitly grants use of the custom fonts.
    if not lic:
        return "no accompanying license"
    data = read(member)
    with NamedTemporaryFile(suffix=PurePosixPath(filename).suffix) as f:
        f.write(data)
        f.flush()
        try:
            info = subprocess.check_output(
                [
                    "fc-scan",
                    "--format",
                    "%{family[0]}|%{style[0]}|%{postscriptname}|%{fontversion}\n",
                    f.name,
                ],
                text=True,
            ).splitlines()
        except subprocess.CalledProcessError:
            return "unreadable font"
    if len(info) != 1 or len(info[0].split("|")) != 4:
        return "internal naming requires review"
    family, style, ps, version = info[0].split("|")
    if (
        "demo" in (label or "").casefold()
        and "demo" not in (family + style + ps).casefold()
    ):
        return "demo edition unconfirmed"
    if not same_face(title, filename, family, style, ps, designer):
        return "internal naming requires review"
    checksum = digest(data)
    if not repair and (norm(ps) in post or checksum in hashes):
        return "already in library"
    dest = Path(slug) / filename
    lp = Path(slug) / PurePosixPath(lic).name
    atomic(out / lp, read(lic))
    atomic(out / dest, data)
    db.execute(
        "INSERT OR REPLACE INTO faces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            slug,
            filename,
            ps,
            family,
            style,
            version,
            checksum,
            source,
            rev,
            member,
            url,
            designer,
            website,
            str(dest),
            str(lp),
        ),
    )
    post.add(norm(ps))
    hashes.add(checksum)
    return "verified"


def register(index, db, out):
    with index:
        index.execute(
            "CREATE TABLE IF NOT EXISTS local_editions(slug TEXT, postscript TEXT, path TEXT, sha256 TEXT, foundry_source TEXT, PRIMARY KEY(slug,postscript))"
        )
        for slug, ps, path, sha, source, rev, member in db.execute(
            "SELECT slug,postscript,path,sha256,source,revision,member FROM faces"
        ):
            origin = (
                source
                if source.startswith("https:")
                else f"https://github.com/{source}/blob/{rev}/" + quote(member)
            )
            index.execute(
                "INSERT INTO local_editions VALUES(?,?,?,?,?) ON CONFLICT(slug,postscript) DO UPDATE SET sha256=excluded.sha256,foundry_source=excluded.foundry_source WHERE local_editions.path=excluded.path",
                (slug, ps, str(out / path), sha, origin),
            )


def run(args):
    root = args.library.resolve()
    out = root / "downloads/Foundry editions/batch"
    out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    lock = (out / ".lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    index = sqlite3.connect(
        (root / "downloads/DaFont/catalog.sqlite").as_uri() + "?mode=rw", uri=True
    )
    db = sqlite3.connect(out / "progress.sqlite")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS listings(url TEXT PRIMARY KEY, names TEXT);
    CREATE TABLE IF NOT EXISTS decisions(slug TEXT, filename TEXT, reason TEXT, PRIMARY KEY(slug,filename));
    CREATE TABLE IF NOT EXISTS faces(slug TEXT, filename TEXT, postscript TEXT, family TEXT, style TEXT, version TEXT, sha256 TEXT, source TEXT, revision TEXT, member TEXT, listing TEXT, author TEXT, author_url TEXT, path TEXT, license_path TEXT, PRIMARY KEY(slug,filename));
    """)
    names, post, hashes = inventory(root, out)
    policy = None
    added = 0
    pending = []
    for designer, author, source in SOURCES:
        try:
            rev, paths, read = snapshot(source, args.cache)
        except (
            subprocess.CalledProcessError,
            requests.RequestException,
            ValueError,
        ) as exc:
            print(f"{designer}: source pending ({exc})", flush=True)
            pending.append(designer)
            continue
        candidates = {}
        for p in paths:
            if (
                p.lower().endswith((".otf", ".ttf"))
                and "__MACOSX" not in p
                and not PurePosixPath(p).name.startswith(".")
            ):
                candidates.setdefault(key(p), []).append(p)
        rows = index.execute(
            "SELECT slug,name,source,author_website,label FROM fonts WHERE designer=? ORDER BY slug",
            (designer,),
        )
        for slug, title, url, website, label in rows:
            parts = urlsplit(website or "")
            if (parts.netloc.removeprefix("www.") + parts.path).strip(
                "/"
            ).casefold() != author.casefold():
                continue
            if (
                not re.fullmatch(r"[a-zA-Z0-9_-]+", slug)
                or urlsplit(url).netloc != "www.dafont.com"
            ):
                continue
            row = db.execute(
                "SELECT names FROM listings WHERE url=?", (url,)
            ).fetchone()
            if row:
                listed = json.loads(row[0])
            else:
                if policy is None:
                    policy = rules(SITE)
                try:
                    body = html(url, policy)
                except requests.HTTPError as exc:
                    if exc.response.status_code not in (404, 410):
                        raise
                    pending.append(slug)
                    print(
                        f"{slug}: listing unavailable ({exc.response.status_code})",
                        flush=True,
                    )
                    continue
                listed = [
                    clean(s)
                    for s in re.findall(
                        r'class="filename"[^>]*>(.*?)</span>', body, re.S
                    )
                ]
                if not listed:
                    pending.append(slug)
                    print("No visible filenames:", slug, flush=True)
                    continue
                db.execute(
                    "INSERT INTO listings VALUES(?,?)", (url, json.dumps(listed))
                )
                db.commit()
            for filename in listed:
                if PurePosixPath(filename).name != filename or key(filename)[1] not in (
                    ".ttf",
                    ".otf",
                ):
                    continue
                previous = db.execute(
                    "SELECT path,sha256,license_path,postscript,source FROM faces WHERE slug=? AND filename=?",
                    (slug, filename),
                ).fetchone()
                if previous and previous[4] != source:
                    continue
                if (
                    previous
                    and (out / previous[0]).is_file()
                    and digest((out / previous[0]).read_bytes()) == previous[1]
                    and (out / previous[2]).is_file()
                ):
                    post.add(norm(previous[3]))
                    continue
                matches = candidates.get(key(filename), [])
                if (
                    not matches
                    and source.startswith("https:")
                    and source.endswith("2026i.zip")
                ):
                    # The two disjoint desktop bundles share the same catalogue listings.
                    continue
                reason = import_face(
                    db,
                    out,
                    (slug, title, url, website, label),
                    filename,
                    (designer, source, rev),
                    paths,
                    matches,
                    read,
                    (names, post, hashes),
                    bool(previous),
                )
                if reason == "verified":
                    added += 1
                db.execute(
                    "INSERT OR REPLACE INTO decisions VALUES(?,?,?)",
                    (slug, filename, reason),
                )
                db.commit()
        print(designer, "complete;", added, "files written this run", flush=True)
    register(index, db, out)
    print(
        "Collection:",
        db.execute("SELECT count(*),count(distinct slug) FROM faces").fetchone(),
    )
    print(
        "Decisions:",
        db.execute("SELECT reason,count(*) FROM decisions GROUP BY reason").fetchall(),
    )

    index.close()
    db.close()
    lock.close()
    if pending:
        raise SystemExit(
            f"{len(pending)} pending sources or listings: " + ", ".join(pending)
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/dafont-foundry"
    )
    run(parser.parse_args())
