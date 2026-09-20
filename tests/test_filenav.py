import hashlib
import json
import tempfile
import zlib
import zipfile
from datetime import datetime

import pytest

import filenav
from filenavlib import archives, hashing, scanner
from filenavlib.errorlog import ErrorSink
from filenavlib.scanner import ScanOptions, walk_root
from filenavlib.writer import JsonScanWriter, _sanitize


def test_skip_marker_excludes_folder_and_subtree(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "file.txt").write_text("hello")

    skip_dir = tmp_path / "skip"
    skip_dir.mkdir()
    (skip_dir / ".filenav-skip").write_text("")
    (skip_dir / "secret.txt").write_text("nope")
    (skip_dir / "sub").mkdir()
    (skip_dir / "sub" / "nested.txt").write_text("also nope")

    opts = ScanOptions()
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archive_entries": 0,
              "archive_entry_bytes": 0,
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

    opts = ScanOptions(ignore_dir_names=frozenset({"node_modules"}))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archive_entries": 0,
              "archive_entry_bytes": 0,
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
    rc = filenav.main([str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0"])
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
        str(output_path), "--root", str(root), "--no-media", "--no-default-ignores", "--no-timestamp-prefix", "--progress-every", "0",
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
        "--ignore-dirs", "my_scratch_folder", "--no-timestamp-prefix", "--progress-every", "0",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "keep.txt" in filenames
    assert "temp.txt" not in filenames


def test_sanitize_replaces_lone_surrogates_without_raising():
    bad = "bad\udcffname.txt"  # what surrogateescape decoding of an invalid byte produces
    cleaned = _sanitize(bad)
    assert cleaned != bad
    json.dumps(cleaned)  # must not raise


def test_writer_handles_lone_surrogate_in_a_record(tmp_path):
    out = tmp_path / "out.json"
    writer = JsonScanWriter(str(out), {"scan_started": "now"})
    writer.write_record({"type": "file", "path": "C:\\weird\\bad\udcffname.txt", "filename": "bad\udcffname.txt"})
    writer.close([], {"files_recorded": 1})

    with open(out, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["files"]) == 1


def test_one_bad_file_does_not_abort_the_whole_walk(tmp_path, monkeypatch):
    (tmp_path / "good1.txt").write_text("a")
    (tmp_path / "bad.txt").write_text("b")
    (tmp_path / "good2.txt").write_text("c")

    real_build = scanner.build_file_record

    def flaky_build(fpath, opts):
        if fpath.endswith("bad.txt"):
            raise RuntimeError("simulated unexpected failure")
        return real_build(fpath, opts)

    monkeypatch.setattr(scanner, "build_file_record", flaky_build)

    opts = ScanOptions()
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archive_entries": 0,
              "archive_entry_bytes": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    names = {r["filename"] for r in records}
    assert names == {"good1.txt", "good2.txt"}
    assert len(errors) == 1
    assert "bad.txt" in errors[0]["path"]


def test_main_recovers_from_fatal_error_mid_scan_and_still_closes_json(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")

    def exploding_walk_root(*args, **kwargs):
        raise RuntimeError("simulated catastrophic failure")

    monkeypatch.setattr(filenav, "walk_root", exploding_walk_root)

    output_path = tmp_path / "out.json"
    rc = filenav.main([str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0"])
    assert rc == 1

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["summary"]["aborted"] is True
    assert "simulated catastrophic failure" in data["summary"]["abort_reason"]
    # the output file's own self-reference record is still written even after the abort
    assert any(r.get("self_reference") for r in data["files"])


def test_error_sink_streams_to_sidecar_log_without_unbounded_memory(tmp_path):
    log_path = tmp_path / "out.errors.log"
    sink = ErrorSink(str(log_path), sample_limit=2)
    for i in range(5):
        sink.append({"path": f"C:\\bad{i}.txt", "error": "boom"})
    sink.close()

    assert sink.count == 5
    assert len(sink.sample) == 2  # bounded, even though 5 errors happened

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    for line in lines:
        json.loads(line)  # every line is its own valid JSON object


def test_main_writes_sidecar_error_log_next_to_output(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "good.txt").write_text("ok")
    (root / "bad.txt").write_text("boom")

    real_build = scanner.build_file_record

    def flaky_build(fpath, opts):
        if fpath.endswith("bad.txt"):
            raise RuntimeError("simulated failure for error-log test")
        return real_build(fpath, opts)

    monkeypatch.setattr(scanner, "build_file_record", flaky_build)

    output_path = tmp_path / "out.json"
    rc = filenav.main([str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0"])
    assert rc == 0

    error_log_path = tmp_path / "out.errors.log"
    assert error_log_path.exists()
    log_lines = error_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 1
    logged = json.loads(log_lines[0])
    assert "bad.txt" in logged["path"]

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["summary"]["error_log_file"] == str(error_log_path)
    assert data["summary"]["errors"] == 1
    assert data["summary"]["errors_sample_truncated"] is False
    assert len(data["errors"]) == 1
    assert "bad.txt" in data["errors"][0]["path"]


def test_apply_timestamp_prefix_formats_local_time_24h():
    when = datetime(2026, 1, 5, 7, 8, 9)
    result = filenav.apply_timestamp_prefix("C:\\scans\\out.json", when)
    assert result == "C:\\scans\\2026-01-05-07-08-09_out.json"

    when_pm = datetime(2026, 12, 31, 23, 59, 0)
    result_pm = filenav.apply_timestamp_prefix("out.json", when_pm)
    assert result_pm == "2026-12-31-23-59-00_out.json"


def test_timestamp_prefix_is_on_by_default_for_output_and_error_log(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")

    before = datetime.now()
    rc = filenav.main([str(tmp_path / "out.json"), "--root", str(root), "--no-media", "--progress-every", "0"])
    after = datetime.now()
    assert rc == 0

    json_files = list(tmp_path.glob("*_out.json"))
    log_files = list(tmp_path.glob("*_out.errors.log"))
    assert len(json_files) == 1
    assert len(log_files) == 1

    prefix = json_files[0].name[: len("yyyy-mm-dd-hh-mm-ss")]
    assert log_files[0].name.startswith(prefix)
    stamp = datetime.strptime(prefix, "%Y-%m-%d-%H-%M-%S")
    assert before.replace(microsecond=0) <= stamp <= after.replace(microsecond=0)

    with open(json_files[0], encoding="utf-8") as f:
        data = json.load(f)
    assert data["summary"]["output_file"] == str(json_files[0])
    assert data["summary"]["error_log_file"] == str(log_files[0])


def test_no_timestamp_prefix_flag_uses_exact_filename(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("hello")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
    ])
    assert rc == 0
    assert output_path.exists()
    assert (tmp_path / "out.errors.log").exists()
    assert list(tmp_path.glob("2*_out.json")) == []
