"""Exercise the public CLI workflow against a synthetic catalogue and archive."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import puller


class PullerTest(unittest.TestCase):
    def test_archive_resume_and_host_policy(self):
        archive = BytesIO()
        with ZipFile(archive, "w") as file:
            file.writestr("example.ttf", b"fixture")
        allowed = False
        visited = []

        def serve(url):
            visited.append(url)
            if url.endswith("robots.txt"):
                rule = "/" if url.startswith(puller.DL) and not allowed else ""
                return SimpleNamespace(text="User-agent: *\nDisallow: "+rule)
            if url.endswith("themes.php"):
                body = '<a href="mtheme.php?id=1">Fancy</a><a href="theme.php?cat=101">Cartoon</a><a href="mtheme.php?id=8">Holiday</a><a href="theme.php?cat=804">Christmas</a>'
            elif url.startswith(puller.DL):
                return SimpleNamespace(content=archive.getvalue())
            else:
                self.assertIn("cat=101",url)
                slug = "first" if "page=1&" in url else "second"
                body = f'<div class="lv1left"><a href="{slug}.font"><strong>{slug}</strong></a></div><a href="//dl.dafont.com/dl/?f={slug}">Download</a>'
                if slug == "first":
                    body += '<a title="Keyboard shortcut: Right arrow" href="/theme.php?cat=101&page=2">Next</a>'
            return SimpleNamespace(content=body.encode())

        with TemporaryDirectory() as output, patch.object(puller,"get",serve), patch("time.sleep"), redirect_stdout(StringIO()):
            def cli(*args):
                with patch("sys.argv",["puller.py",*args,"--output",output]):
                    puller.main()
            cli("index","--pages","1")
            cli("index")
            self.assertEqual(sum("page=1&" in u for u in visited),1)
            self.assertEqual(sum("page=2&" in u for u in visited),1)
            with self.assertRaisesRegex(RuntimeError,"disallows"):
                cli("pull")
            allowed = True
            cli("pull")
            self.assertEqual(len(list(Path(output).glob("*.zip"))),2)
            (Path(output)/"first.zip").write_bytes(b"interrupted")
            visited.clear()
            cli("pull")
            self.assertEqual(sum("/dl/?" in u for u in visited),1)
            puller.inspect_zip(Path(output)/"first.zip")

if __name__ == "__main__":
    unittest.main()
