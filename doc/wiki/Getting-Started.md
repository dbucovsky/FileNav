# Getting Started

## Requirements

- Python 3.9+
- [7-Zip](https://www.7-zip.org/) for `.7z`/`.rar` archive contents (optional —
  without it those files are still recorded, just not opened up)
- The packages in `requirements.txt`

## Install

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Run a scan

```
.venv\Scripts\python filenav.py output.json
```

The output filename is even optional — leave it out and it defaults to
`output.json`:

```
.venv\Scripts\python filenav.py
```

With no `--root`, this scans the whole `C:\` drive, which can take a long time
on a real machine. For a first try, scope it down:

```
.venv\Scripts\python filenav.py output.json --root C:\Users\me\Documents
```

Whatever filename you use gets a `yyyy-mm-dd-hh-mm-ss_` timestamp prefix by
default (so repeated runs never overwrite each other) — the real file that
gets written is printed at the end of the run. Open it afterward — it's
plain JSON, see [[Usage]] for the shape.

Two more files appear alongside it automatically: `<output>.errors.log`
(anything that couldn't be read, one JSON object per line) and `<output>.log`
(a diagnostic log of what the scan was doing — see [[Usage]] if a scan ever
seems stuck).

## Excluding folders and files

Three ways, from simplest to most flexible:

- **One specific folder**: drop an empty file named `.filenav-skip` inside it.
- **Every folder with a given name, anywhere**: `--ignore-dirs Backups
  OldProjects` (a sensible default list — `.git`, `node_modules`,
  `__pycache__`, Recycle Bin, etc. — is already applied automatically).
- **A path or wildcard pattern**: `--exclude-paths "C:\Users\*\Downloads\*"
  "*.tmp"`.

## Running the same scan repeatedly

Rather than retyping a long command every time, put your options in a JSON
file and point `--config` at it:

```
.venv\Scripts\python filenav.py --config myscan.json
```

```jsonc
// myscan.json
{
  "root": ["C:\\", "D:\\"],
  "ignore_dirs": ["Backups"],
  "exclude_paths": ["C:\\Users\\*\\Downloads\\*"]
}
```

Anything you also pass on the command line overrides the same setting in the
file for that one run.

Next: [[Usage]] for the full flag reference, config file precedence rules,
and output JSON shape.
