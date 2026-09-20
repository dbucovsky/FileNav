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

With no `--root`, this scans the whole `C:\` drive, which can take a long time
on a real machine. For a first try, scope it down:

```
.venv\Scripts\python filenav.py output.json --root C:\Users\me\Documents
```

Open `output.json` afterward — it's plain JSON, see [[Usage]] for the shape.

## Excluding a folder

Create an empty file named `.filenav-skip` inside any folder you want left out
of the scan entirely (that folder and everything under it).

Next: [[Usage]] for the full flag reference and output format.
