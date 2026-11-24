# DaFont Puller

Build a resumable local catalogue of DaFont by theme, from the terminal. Records include the family name, source page, theme, licence label, and the designer, author profile and website where credited. The index covers the published theme directory including Bitmap and excludes Dingbats and Holiday. All licence labels are eligible.

## Quick start

Requires Python 3.9+ and Git.

```sh
git clone https://github.com/dopexthrone/dafont-puller.git
cd dafont-puller
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python puller.py index
python puller.py status
```

The default output directory is `~/DaFont`. Pass the same `--output DIRECTORY` to each command to use another location. Each page commits its entries and next page number together to SQLite. Press Ctrl-C to stop and rerun the index command to resume. Run one indexer per output directory. The catalogue reflects pages as retrieved and DaFont can change during a run.

Upgrading an existing index preserves its font records and restarts the page checkpoints once to collect designer credits. Run the index command again to backfill. Missing credits and links are stored as NULL.

Index two pages of the Sans serif theme as a quick trial, then continue the full index.

```sh
python puller.py index --theme 501 --pages 2
python puller.py index
```

Read the source links from the local catalogue using the optional SQLite CLI.

```sh
sqlite3 -header -column ~/DaFont/catalog.sqlite 'SELECT name, label, source FROM fonts LIMIT 10'
```

## Archive retrieval

DaFont currently disallows automated ZIP downloads on [its download host](https://dl.dafont.com/robots.txt). Use the catalogue’s individual source links to obtain archives manually. A full local catalogue can contain more entries than your downloaded collection.

The `pull` command checks the download host’s robots policy and exits when a missing archive is disallowed. For permitted requests it validates the original ZIP, checks for font files, and saves it atomically. Valid existing archives are reused. Use `python puller.py pull --workers 4` to run it. Four is the maximum concurrent archive requests. Listing requests are sequential.

This project distributes the MIT-licensed tool. Font usage terms come from their respective authors.

## Import missing faces from foundries

On Linux, install Fontconfig (for `fc-list` and `fc-scan`) and the GitHub CLI (`gh`, signed in for public API reads). Point the command at a local library containing an index at `downloads/DaFont/catalog.sqlite`. To create that index first, use `puller.py index --output "$LIBRARY/downloads/DaFont"`.

```sh
python3 foundry.py --library "$LIBRARY"
```

Stop with Ctrl-C and repeat the same command to resume. It keeps listing filenames in SQLite and pins GitHub trees and downloaded blobs in `~/.cache/dafont-foundry`. Pass `--cache DIRECTORY` to choose a different cache. It reads only reviewed sources connected to the indexed designer and website. These include GGBotNet's CC0 repository, P. Baudin's ACSF project, Televo's font collection and both desktop collections on [Typodermic’s download page](https://typodermicfonts.com/downloads/). The two Typodermic collections carry different terms.

Each saved desktop file must match a filename and extension on the listing and carry consistent internal family and face names. The tool compares existing filenames in the library and its ZIPs, installed PostScript names, and available catalogue hashes. Ambiguous matches remain in the decisions table. This verifies the named face and records the publisher revision. The listing does not establish the binary revision of a DaFont download.

Results go under `downloads/Foundry editions/batch/`, grouped by listing with the publisher's accompanying terms. The `faces` table in `progress.sqlite` records provenance, version, paths and SHA-256. The primary catalogue's `local_editions` table links to the files. To inspect coverage:

```sh
sqlite3 -header -column "$LIBRARY/downloads/Foundry editions/batch/progress.sqlite" \
  'SELECT author, COUNT(*) AS faces, COUNT(DISTINCT slug) AS listings FROM faces GROUP BY author;'
```
