"""Exercise a publisher import, interruption recovery, and edition holds."""

import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import foundry


class ImportTest(unittest.TestCase):
    def test_resume_and_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "downloads/DaFont/catalog.sqlite"
            index.parent.mkdir(parents=True)
            with sqlite3.connect(index) as db:
                db.execute(
                    "CREATE TABLE fonts(slug,name,source,author_website,designer,label)"
                )
                db.execute(
                    "INSERT INTO fonts VALUES('example','Example','https://www.dafont.com/example.font','https://publisher.test','Author','100% Free')"
                )
            args = SimpleNamespace(library=root, cache=root / "cache")
            files = {
                "Example-Regular.ttf": b"regular",
                "Example-Demo.ttf": b"full",
                "LICENSE": b"terms",
            }

            def checked(*a, **kw):
                return "Example|Regular|Example-Regular|65536\n"

            listing = "".join(
                f'<span class="filename">{name}</span>'
                for name in [
                    "Example-Regular.ttf",
                    "Example-Demo.ttf",
                    "Example-Semibold.ttf",
                ]
            )
            with (
                patch.object(
                    foundry, "SOURCES", [("Author", "publisher.test", "publisher/repo")]
                ),
                patch.object(
                    foundry,
                    "snapshot",
                    return_value=("revision", list(files), files.__getitem__),
                ),
                patch.object(foundry, "html", return_value=listing) as fetch,
                patch.object(foundry, "rules"),
                patch.object(
                    foundry, "inventory", side_effect=lambda *a: (set(), set(), set())
                ),
                patch.object(subprocess, "check_output", side_effect=checked),
            ):
                foundry.run(args)
                out = root / "downloads/Foundry editions/batch"
                dest = out / "example/Example-Regular.ttf"
                self.assertEqual(dest.read_bytes(), b"regular")
                self.assertFalse((dest.parent / "Example-Demo.ttf").exists())
                stat = dest.stat()
                foundry.run(args)
                self.assertEqual(dest.stat().st_mtime_ns, stat.st_mtime_ns)
                dest.write_bytes(b"damaged")
                foundry.run(args)
                self.assertEqual(dest.read_bytes(), b"regular")
                self.assertEqual(fetch.call_count, 1)
                with sqlite3.connect(index) as db:
                    self.assertEqual(
                        db.execute("SELECT count(*) FROM local_editions").fetchone()[0],
                        1,
                    )
