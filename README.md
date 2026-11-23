# DaFont Puller

Build a resumable local catalogue of DaFont by theme, from the terminal. Records include the family name, source page, theme and licence label. The index covers the published theme directory including Bitmap and excludes Dingbats and Holiday. All licence labels are eligible.

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
