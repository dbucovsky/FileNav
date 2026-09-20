import hashlib
import json
import tempfile
import zlib
import zipfile

import pytest

import filenav
from filenavlib import archives, hashing
from filenavlib.scanner import ScanOptions, walk_root


def test_skip_marker_excludes_folder_and_subtree(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "file.txt").write_text("hello")

    skip_dir = tmp_path / "skip"
    skip_dir.mkdir()
    (skip_dir / ".filenav-skip").write_text("")
    (skip_dir / "secret.txt").write_text("nope")
    (skip_dir / "sub").mkdir()
    (skip_dir / "sub" / "nested.txt").write_text("also nope")

    opts = ScanOptions(self_path_norm="")
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "errors": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    names = {r["filename"] for r in records}
    assert names == {"file.txt"}
    assert stats["skipped_dirs_marker"] == 1


def test_ignore_dir_names_excludes_folder_and_subtree(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "file.txt").write_text("hello")

    noisy = tmp_path / "node_modules"
    noisy.mkdir()
    (noisy / "package.js").write_text("noise")
    (noisy / "sub").mkdir()
    (noisy / "sub" / "nested.js").write_text("also noise")

    opts = ScanOptions(self_path_norm="", ignore_dir_names=frozenset({"node_modules"}))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "errors": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    names = {r["filename"] for r in records}
    assert names == {"file.txt"}
    assert stats["skipped_dirs_ignore_list"] == 1


def test_hash_file_matches_hashlib(tmp_path):
    f = tmp_path / "data.bin"
    content = b"the quick brown fox jumps over the lazy dog" * 100
    f.write_bytes(content)

    digest, reason = hashing.hash_file(str(f), "sha256")
    assert reason is None
    assert digest == hashlib.sha256(content).hexdigest()


def test_hash_file_skips_when_over_size_limit(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 1000)

    digest, reason = hashing.hash_file(str(f), "sha256", max_size_bytes=10)
    assert digest is None
    assert "skipped" in reason


def test_nested_zip_archive_is_expanded_recursively(tmp_path):
    inner_content = b"inner file contents"
    inner_path = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_path, "w") as zf:
        zf.writestr("a.txt", inner_content)

    outer_path = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer_path, "w") as zf:
        zf.write(inner_path, arcname="inner.zip")
        zf.writestr("b.txt", b"top level file")

    opts = ScanOptions()
    records = []
    errors = []
    with tempfile.TemporaryDirectory() as scratch:
        archives.process_archive_file(str(outer_path), [str(outer_path)], 1, opts, records.append, errors, scratch)

    paths = {r["path"] for r in records}
    assert any(p.endswith("b.txt") for p in paths)
    assert any(p.endswith("inner.zip") for p in paths)
    assert any(p.endswith("inner.zip // a.txt") for p in paths)

    a_record = next(r for r in records if r["path"].endswith("a.txt"))
    assert a_record["hash_algo"] == "crc32"
    assert a_record["hash"] == f"{zlib.crc32(inner_content) & 0xFFFFFFFF:08x}"
    assert a_record["nesting_depth"] == 2
    assert not errors


def test_resolve_output_path_bare_name_uses_script_dir():
    result = filenav.resolve_output_path("out.json", "C:\\scripts")
    assert result == "C:\\scripts\\out.json"


def test_resolve_output_path_with_directory_uses_given_path(tmp_path):
    target = tmp_path / "sub" / "out.json"
    result = filenav.resolve_output_path(str(target), "C:\\scripts")
    assert result == str(target)


def test_main_end_to_end_produces_valid_json_with_self_record(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello world")

    skip_dir = root / "skip_me"
    skip_dir.mkdir()
    (skip_dir / ".filenav-skip").write_text("")
    (skip_dir / "ignored.txt").write_text("ignored")

    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]")

    with zipfile.ZipFile(root / "archive.zip", "w") as zf:
        zf.writestr("inside.txt", b"zip contents")

    output_path = tmp_path / "out.json"
    rc = filenav.main([str(output_path), "--root", str(root), "--no-media", "--progress-every", "0"])
    assert rc == 0
    assert output_path.exists()

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "a.txt" in filenames
    assert "ignored.txt" not in filenames
    assert "config" not in filenames  # inside .git, skipped by the default ignore list
    assert "archive.zip" in filenames
    assert "inside.txt" in filenames
    assert data["summary"]["directories_skipped_via_ignore_list"] >= 1
    assert "out.json" in filenames

    self_records = [r for r in data["files"] if r.get("self_reference")]
    assert len(self_records) == 1
    assert self_records[0]["path"] == str(output_path)

    assert data["summary"]["files_recorded"] >= 3
    assert data["summary"]["archives_expanded"] == 1
    assert data["summary"]["archive_entries_recorded"] >= 1


def test_no_default_ignores_flag_allows_git_contents_through(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-default-ignores", "--progress-every", "0",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "config" in filenames


def test_ignore_dirs_flag_adds_a_custom_name(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    custom = root / "my_scratch_folder"
    custom.mkdir()
    (custom / "temp.txt").write_text("noise")
    (root / "keep.txt").write_text("real data")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media",
        "--ignore-dirs", "my_scratch_folder", "--progress-every", "0",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "keep.txt" in filenames
    assert "temp.txt" not in filenames
