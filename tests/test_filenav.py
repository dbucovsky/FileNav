import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import zlib
import zipfile
from datetime import datetime

import pytest

import filenav
from filenavlib import archives, dateanalysis, hashing, scanner
from filenavlib.archives import find_seven_zip
from filenavlib.configfile import ConfigError, load_config
from filenavlib.errorlog import ErrorSink
from filenavlib.scanlog import setup_logging
from filenavlib.scanner import ScanOptions, matches_any_pattern, walk_root
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
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0, "skipped_files_path_pattern": 0, "self_deferred": False}

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
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0, "skipped_files_path_pattern": 0, "self_deferred": False}

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

    def flaky_build(fpath, opts, *args):
        if fpath.endswith("bad.txt"):
            raise RuntimeError("simulated unexpected failure")
        return real_build(fpath, opts, *args)

    monkeypatch.setattr(scanner, "build_file_record", flaky_build)

    opts = ScanOptions()
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0, "skipped_files_path_pattern": 0, "self_deferred": False}

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

    def flaky_build(fpath, opts, *args):
        if fpath.endswith("bad.txt"):
            raise RuntimeError("simulated failure for error-log test")
        return real_build(fpath, opts, *args)

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


def test_nested_archive_cleanup_removes_the_whole_temp_dir(tmp_path):
    # Regression test: cleanup used to do shutil.rmtree(os.path.dirname(extracted_path)),
    # which only removes the member's immediate parent -- when the nested archive's
    # own internal name has subdirectory components (very common in real zips),
    # that left the actual mkdtemp() directory behind forever, leaking one
    # directory into the scratch folder per nested archive for the rest of the run.
    inner_content = b"inner file contents"
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as zf:
        zf.writestr("a.txt", inner_content)

    outer_path = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer_path, "w") as zf:
        zf.writestr("sub/dir/inner.zip", inner_buf.getvalue())

    opts = ScanOptions()
    records = []
    errors = []
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    archives.process_archive_file(
        str(outer_path), [str(outer_path)], 1, opts, records.append, errors, str(scratch_root)
    )

    assert not errors
    assert any(r["path"].endswith("inner.zip // a.txt") for r in records)
    assert list(scratch_root.iterdir()) == []


def test_setup_logging_writes_to_file(tmp_path):
    log_path = tmp_path / "run.log"
    logger = setup_logging(str(log_path))
    logger.info("hello world")
    for h in logger.handlers:
        h.flush()

    content = log_path.read_text(encoding="utf-8")
    assert "hello world" in content

    for h in logger.handlers[:]:
        h.close()


def test_log_file_has_per_directory_lines_but_not_per_file_by_default(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
    ])
    assert rc == 0

    log_content = (tmp_path / "out.log").read_text(encoding="utf-8")
    assert "Scanning directory" in log_content
    assert str(root / "a.txt") not in log_content


def test_verbose_flag_adds_per_file_log_lines(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix",
        "--verbose", "--progress-every", "0",
    ])
    assert rc == 0

    log_content = (tmp_path / "out.log").read_text(encoding="utf-8")
    assert str(root / "a.txt") in log_content


def test_slow_hash_triggers_a_warning_log_line(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "slow.txt").write_text("x")

    real_hash_file = hashing.hash_file

    def slow_hash_file(*args, **kwargs):
        time.sleep(0.05)
        return real_hash_file(*args, **kwargs)

    monkeypatch.setattr(hashing, "hash_file", slow_hash_file)

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix",
        "--slow-threshold-seconds", "0.01", "--progress-every", "0",
    ])
    assert rc == 0

    log_content = (tmp_path / "out.log").read_text(encoding="utf-8")
    assert "WARNING" in log_content
    assert "Slow hash" in log_content


def test_heartbeat_logs_progress_with_a_short_interval(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    # Real wall-clock timing is flaky for a sub-millisecond threshold on a
    # single tiny file -- fake the clock instead so every tick reliably
    # advances past the (deliberately tiny) heartbeat interval.
    fake_now = [0.0]

    def fake_monotonic():
        fake_now[0] += 1.0
        return fake_now[0]

    monkeypatch.setattr(filenav.time, "monotonic", fake_monotonic)

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix",
        "--log-interval", "0.5", "--progress-every", "0",
    ])
    assert rc == 0

    log_content = (tmp_path / "out.log").read_text(encoding="utf-8")
    assert "Heartbeat" in log_content


def test_archive_members_under_an_ignored_dir_name_are_filtered(tmp_path):
    # Regression test: the ignore-dir-name list (.git, __pycache__, etc.) only
    # applied to real filesystem directories in scanner.py -- archive contents
    # enumerated by archives.py had no filtering at all, so a __pycache__ or
    # .git folder bundled inside any zip/tar/7z/rar (e.g. a Python wheel,
    # sdist, or downloaded source archive) would leak those entries through.
    archive_path = tmp_path / "wheel.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("mypkg/__init__.py", b"real code")
        zf.writestr("mypkg/__pycache__/__init__.cpython-311.pyc", b"cached bytecode")
        zf.writestr("mypkg/.git/config", b"[core]")

    opts = ScanOptions(ignore_dir_names=frozenset({"__pycache__", ".git"}))
    records = []
    errors = []
    stats = {"archive_entries_skipped_ignore_list": 0}
    with tempfile.TemporaryDirectory() as scratch:
        archives.process_archive_file(
            str(archive_path), [str(archive_path)], 1, opts, records.append, errors, scratch, stats
        )

    paths = {r["path"] for r in records}
    assert any(p.endswith("__init__.py") for p in paths)
    assert not any("__pycache__" in p for p in paths)
    assert not any(".git" in p for p in paths)
    assert stats["archive_entries_skipped_ignore_list"] == 2
    assert not errors


def test_main_end_to_end_filters_ignored_dirs_inside_archives(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with zipfile.ZipFile(root / "wheel.zip", "w") as zf:
        zf.writestr("mypkg/__init__.py", b"real code")
        zf.writestr("mypkg/__pycache__/__init__.cpython-311.pyc", b"cached bytecode")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    paths = {rec["path"] for rec in data["files"]}
    assert any(p.endswith("__init__.py") for p in paths)
    assert not any("__pycache__" in p for p in paths)
    assert data["summary"]["archive_entries_skipped_ignore_list"] == 1


def test_load_config_parses_valid_file_and_widens_ints_to_float(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "root": ["C:\\", "D:\\Photos"],
        "max_hash_size_mb": 4096,  # JSON int, schema wants float
        "verbose": True,
        "ignore_dirs": "SingleName",  # bare string shorthand for a one-item list
    }), encoding="utf-8")

    result = load_config(str(config_path), filenav.CONFIG_SCHEMA)

    assert result["root"] == ["C:\\", "D:\\Photos"]
    assert result["max_hash_size_mb"] == 4096.0
    assert isinstance(result["max_hash_size_mb"], float)
    assert result["verbose"] is True
    assert result["ignore_dirs"] == ["SingleName"]


def test_load_config_rejects_unknown_key(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"not_a_real_option": 1}), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown option"):
        load_config(str(config_path), filenav.CONFIG_SCHEMA)


def test_load_config_rejects_wrong_type(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"verbose": "yes"}), encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a bool"):
        load_config(str(config_path), filenav.CONFIG_SCHEMA)


def test_load_config_rejects_invalid_json(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(str(config_path), filenav.CONFIG_SCHEMA)


def test_load_config_rejects_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="could not read"):
        load_config(str(tmp_path / "missing.json"), filenav.CONFIG_SCHEMA)


def test_config_file_supplies_ignore_dirs_and_output(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    custom = root / "my_scratch_folder"
    custom.mkdir()
    (custom / "temp.txt").write_text("noise")
    (root / "keep.txt").write_text("real data")

    output_path = tmp_path / "out.json"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "output": str(output_path),
        "root": [str(root)],
        "ignore_dirs": ["my_scratch_folder"],
        "no_media": True,
        "no_timestamp_prefix": True,
        "progress_every": 0,
    }), encoding="utf-8")

    rc = filenav.main(["--config", str(config_path)])
    assert rc == 0
    assert output_path.exists()

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "keep.txt" in filenames
    assert "temp.txt" not in filenames
    assert data["options"]["config_file"] == str(config_path)


def test_cli_flag_overrides_same_option_in_config_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"hash_algo": "md5"}), encoding="utf-8")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--config", str(config_path),
        "--hash-algo", "sha256", "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["options"]["hash_algo"] == "sha256"


def test_missing_output_falls_back_to_output_json_with_timestamp_prefix(tmp_path, monkeypatch):
    # A bare default filename is written next to filenav.py -- fake __file__
    # so that "next to filenav.py" resolves inside tmp_path, not the real repo.
    monkeypatch.setattr(filenav, "__file__", str(tmp_path / "fake_filenav.py"))

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    rc = filenav.main(["--root", str(root), "--no-media", "--progress-every", "0"])
    assert rc == 0

    json_files = list(tmp_path.glob("*_output.json"))
    assert len(json_files) == 1
    assert list(tmp_path.glob("*_output.errors.log"))
    assert list(tmp_path.glob("*_output.log"))

    with open(json_files[0], encoding="utf-8") as f:
        data = json.load(f)
    filenames = {rec["filename"] for rec in data["files"]}
    assert "a.txt" in filenames


def test_missing_output_with_no_timestamp_prefix_uses_exact_default_name(tmp_path, monkeypatch):
    monkeypatch.setattr(filenav, "__file__", str(tmp_path / "fake_filenav.py"))

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    rc = filenav.main(["--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0"])
    assert rc == 0
    assert (tmp_path / "output.json").exists()


def test_config_supplies_root_but_not_output_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(filenav, "__file__", str(tmp_path / "fake_filenav.py"))

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("x")

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"root": [str(root)], "no_media": True, "progress_every": 0}),
                            encoding="utf-8")

    rc = filenav.main(["--config", str(config_path), "--no-timestamp-prefix"])
    assert rc == 0
    assert (tmp_path / "output.json").exists()


def test_config_flag_with_missing_file_is_a_clear_error(tmp_path, capsys):
    rc = filenav.main([str(tmp_path / "out.json"), "--config", str(tmp_path / "missing.json")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Error in --config file" in captured.err


def test_matches_any_pattern_supports_glob_wildcards_and_either_slash_style():
    path = r"C:\Users\me\Downloads\file.txt"
    assert matches_any_pattern(path, [r"*\Downloads\*"])
    assert matches_any_pattern(path, ["*/Downloads/*"])  # forward slashes also work on Windows
    assert matches_any_pattern(path, [r"C:\Users\*\Downloads\*"])
    assert not matches_any_pattern(path, [r"*\Uploads\*"])
    assert not matches_any_pattern(path, [])


def test_exclude_path_patterns_skips_a_matching_folder_and_its_subtree(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "file.txt").write_text("hello")

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "movie.mp4").write_text("noise")
    (downloads / "sub").mkdir()
    (downloads / "sub" / "nested.txt").write_text("also noise")

    opts = ScanOptions(exclude_path_patterns=frozenset({str(downloads) + "*"}))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    names = {r["filename"] for r in records}
    assert names == {"file.txt"}
    assert stats["skipped_dirs_path_pattern"] == 1


def test_exclude_path_patterns_skips_a_single_matching_file_only(tmp_path):
    (tmp_path / "keep.txt").write_text("real data")
    (tmp_path / "secret.env").write_text("password=hunter2")

    opts = ScanOptions(exclude_path_patterns=frozenset({"*.env"}))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    names = {r["filename"] for r in records}
    assert names == {"keep.txt"}
    assert stats["skipped_files_path_pattern"] == 1


def test_exclude_paths_flag_end_to_end(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    downloads = root / "Downloads"
    downloads.mkdir()
    (downloads / "movie.mp4").write_text("noise")
    (root / "keep.txt").write_text("real data")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
        "--exclude-paths", str(downloads) + "*",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "keep.txt" in filenames
    assert "movie.mp4" not in filenames
    assert data["summary"]["directories_skipped_via_path_pattern"] == 1


def test_tar_members_are_hashed_in_a_single_pass_not_reopened_per_member(tmp_path, monkeypatch):
    # Regression test: hash_member() used to call tarfile.open(path) fresh for
    # every single member. A compressed tar is not seekable, so reaching
    # member N means decompressing from byte zero every time -- O(n^2)
    # overall. A real 143,770-entry .tar.bz2 was observed taking ~50s *per
    # small file* because of this (on pace for months for the one archive).
    # Members must now come out of a single sequential pass, so the tar file
    # is opened exactly once total, regardless of member count.
    tar_path = tmp_path / "archive.tar"
    with tarfile.open(tar_path, "w") as tf:
        for i in range(5):
            data = f"content {i}".encode()
            info = tarfile.TarInfo(name=f"file{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    open_count = [0]
    real_open = archives.tarfile.open

    def counting_open(*args, **kwargs):
        open_count[0] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(archives.tarfile, "open", counting_open)

    opts = ScanOptions()
    records = []
    errors = []
    with tempfile.TemporaryDirectory() as scratch:
        archives.process_archive_file(str(tar_path), [str(tar_path)], 1, opts, records.append, errors, scratch)

    assert open_count[0] == 1, f"tar was opened {open_count[0]} times, expected exactly 1"
    assert not errors
    assert {r["filename"] for r in records} == {f"file{i}.txt" for i in range(5)}

    for r in records:
        i = int(r["filename"][len("file"):-len(".txt")])
        assert r["hash"] == hashlib.sha256(f"content {i}".encode()).hexdigest()
        assert r["hash_algo"] == "sha256"


def test_no_expand_formats_records_the_archive_file_but_skips_its_contents(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    tar_path = root / "bundle.tar"
    with tarfile.open(tar_path, "w") as tf:
        data = b"inner content"
        info = tarfile.TarInfo(name="inner.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    opts = ScanOptions(no_expand_formats=frozenset({"tar"}))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(root), opts, records.append, errors, scratch, stats)

    assert len(records) == 1
    record = records[0]
    assert record["filename"] == "bundle.tar"
    assert record["archive_format"] == "tar"
    assert record["archive_expansion_skipped"] is True
    assert record["hash"] == hashlib.sha256(tar_path.read_bytes()).hexdigest()  # hashed like any normal file
    assert record["type"] == "file"
    assert stats["archives"] == 0
    assert stats["archives_not_expanded"] == 1
    assert stats["archive_entries"] == 0


def test_no_expand_formats_leaves_other_formats_unaffected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with zipfile.ZipFile(root / "archive.zip", "w") as zf:
        zf.writestr("inside.txt", b"zip contents")

    opts = ScanOptions(no_expand_formats=frozenset({"tar"}))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0, "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(root), opts, records.append, errors, scratch, stats)

    assert any(r["path"].endswith("inside.txt") for r in records)
    assert stats["archives"] == 1
    assert stats["archives_not_expanded"] == 0


def test_no_expand_formats_flag_end_to_end(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with tarfile.open(root / "bundle.tar.gz", "w:gz") as tf:
        data = b"inner content"
        info = tarfile.TarInfo(name="inner.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
        "--no-expand-formats", "tar",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "bundle.tar.gz" in filenames
    assert "inner.txt" not in filenames
    assert not any(rec["type"] == "archive_entry" for rec in data["files"])
    assert data["summary"]["archives_not_expanded"] == 1
    assert data["summary"]["archives_expanded"] == 0
    assert data["options"]["no_expand_formats"] == ["tar"]


def test_password_protected_7z_does_not_hang_and_is_recorded_as_an_error(tmp_path):
    # Regression test for a real incident: a scan got stuck for 28+ minutes
    # (until interrupted) on a password-protected .7z, because the 7z
    # subprocess calls never set stdin=, so 7z's password prompt inherited
    # the real console and blocked waiting for input that would never come.
    seven_zip = find_seven_zip()
    if not seven_zip:
        pytest.skip("7-Zip not installed; can't build a real password-protected archive")

    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"  # built outside root, archived, then discarded
    secret.write_text("secret content")
    protected = root / "protected.7z"
    subprocess.run(
        [seven_zip, "a", "-t7z", "-pTestPass123", "-mhe=on", "--", str(protected), str(secret)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )

    output_path = tmp_path / "out.json"
    start = time.monotonic()
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
    ])
    elapsed = time.monotonic() - start

    assert rc == 0
    assert elapsed < 30, f"took {elapsed:.1f}s -- should fail fast, not approach the 300s hard ceiling"

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    filenames = {rec["filename"] for rec in data["files"]}
    assert "protected.7z" in filenames  # the archive itself is still recorded, just couldn't be opened

    assert data["summary"]["errors"] >= 1
    assert any("protected.7z" in e["path"] for e in data["errors"])


def _make_png_bytes(width=64, height=32, color="red"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_archive_media_disabled_by_default_for_zip(tmp_path):
    png_bytes = _make_png_bytes()
    zip_path = tmp_path / "photos.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.png", png_bytes)

    opts = ScanOptions()  # extract_archive_media_metadata defaults to False
    records = []
    errors = []
    with tempfile.TemporaryDirectory() as scratch:
        archives.process_archive_file(str(zip_path), [str(zip_path)], 1, opts, records.append, errors, scratch)

    photo_record = next(r for r in records if r["filename"] == "photo.png")
    assert "image" not in photo_record


def test_archive_media_extracts_image_info_for_zip_when_enabled(tmp_path):
    png_bytes = _make_png_bytes(width=64, height=32)
    zip_path = tmp_path / "photos.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("photo.png", png_bytes)

    opts = ScanOptions(extract_archive_media_metadata=True)
    records = []
    errors = []
    with tempfile.TemporaryDirectory() as scratch:
        archives.process_archive_file(str(zip_path), [str(zip_path)], 1, opts, records.append, errors, scratch)

    photo_record = next(r for r in records if r["filename"] == "photo.png")
    assert photo_record["image"]["width"] == 64
    assert photo_record["image"]["height"] == 32
    assert not errors


def test_archive_media_extracts_image_info_for_tar_when_enabled(tmp_path):
    # tar goes through a completely different code path (the single sequential
    # listing/hashing pass) -- verify it separately, and specifically confirm
    # buffering the bytes once for both hash and media didn't break hashing.
    png_bytes = _make_png_bytes(width=48, height=24)
    tar_path = tmp_path / "photos.tar"
    with tarfile.open(tar_path, "w") as tf:
        info = tarfile.TarInfo(name="photo.png")
        info.size = len(png_bytes)
        tf.addfile(info, io.BytesIO(png_bytes))

    opts = ScanOptions(extract_archive_media_metadata=True)
    records = []
    errors = []
    with tempfile.TemporaryDirectory() as scratch:
        archives.process_archive_file(str(tar_path), [str(tar_path)], 1, opts, records.append, errors, scratch)

    photo_record = next(r for r in records if r["filename"] == "photo.png")
    assert photo_record["image"]["width"] == 48
    assert photo_record["image"]["height"] == 24
    assert photo_record["hash"] == hashlib.sha256(png_bytes).hexdigest()
    assert not errors


def test_archive_media_flag_end_to_end(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    png_bytes = _make_png_bytes(width=32, height=16)
    with zipfile.ZipFile(root / "photos.zip", "w") as zf:
        zf.writestr("photo.png", png_bytes)

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-timestamp-prefix", "--progress-every", "0",
        "--archive-media",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    photo_record = next(r for r in data["files"] if r["filename"] == "photo.png")
    assert photo_record["image"]["width"] == 32
    assert photo_record["image"]["height"] == 16
    assert data["summary"]["archive_media_extracted"] == 1
    assert data["options"]["archive_media_metadata"] is True


def test_find_dates_in_text_covers_the_real_formats_seen_on_disk():
    def values(text):
        return {c["value"] for c in dateanalysis.find_dates_in_text(text)}

    assert "2019-12-19" in values("2019-12-19 Desktop")
    assert "2026-09-20T14:36:25" in values("2026-09-20-14-36-25_output.json")
    assert "2020-03-20" in values("MU17-VEH-20200320.7z")
    assert "2019-08-12T09:15:41" in values("backup-mon-aug-12-09_15_41-2019.tar")
    assert "2019-08-12" in values("August 12, 2019 report.docx")
    assert "2019-08-12" in values("12-Aug-2019.zip")

    ambiguous = dateanalysis.find_dates_in_text("08-09-2019")
    assert {c["value"] for c in ambiguous} == {"2019-08-09", "2019-09-08"}
    assert all(c["ambiguous"] for c in ambiguous)

    bare_year = dateanalysis.find_dates_in_text("TF-2001")
    assert len(bare_year) == 1
    assert bare_year[0]["confidence"] == "low"
    assert bare_year[0]["pattern"] == "bare_year"

    assert dateanalysis.find_dates_in_text("no_date_here_at_all.txt") == []
    assert dateanalysis.find_dates_in_text("103-01-LR-PWA-BOM-V1") == []


def test_find_dates_in_path_segments_only_returns_dated_segments():
    result = dateanalysis.find_dates_in_path_segments(r"C:\TSoM\Old\Mixed\2018-09-16_Desktop\Work")
    assert len(result) == 1
    assert result[0]["segment"] == "2018-09-16_Desktop"
    assert result[0]["dates"][0]["value"] == "2018-09-16"


def test_compute_flags_narrow_anomalies_only():
    from datetime import datetime as dt

    ref = dt(2026, 9, 24, 12, 0, 0)

    assert dateanalysis.compute_flags(None, "2027-01-01T00:00:00", None, ref) == ["future_date"]
    assert dateanalysis.compute_flags("1970-01-01T00:00:00", "2020-01-01T00:00:00", None, ref) == [
        "default_epoch_date"
    ]
    assert dateanalysis.compute_flags("2020-01-01T00:00:00", "2020-06-01T00:00:00", None, ref) == []

    # The explicitly-agreed non-goals: neither created > modified nor a
    # differing capture date is itself an anomaly (both are normal for a
    # copied/migrated file).
    assert dateanalysis.compute_flags("2024-01-01T00:00:00", "2020-06-01T00:00:00", None, ref) == []
    assert dateanalysis.compute_flags(
        "2020-01-01T00:00:00", "2020-06-01T00:00:00", "2010:01:01 00:00:00", ref
    ) == []


def test_date_analysis_disabled_by_default(tmp_path):
    (tmp_path / "2019-12-19 Desktop").mkdir()
    (tmp_path / "2019-12-19 Desktop" / "report-2020-01-01.txt").write_text("x")

    opts = ScanOptions()  # extract_date_analysis defaults to False
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0,
              "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    record = next(r for r in records if r["filename"] == "report-2020-01-01.txt")
    assert "date_analysis" not in record


def test_date_analysis_collects_filename_and_folder_dates_and_flags_when_enabled(tmp_path):
    from datetime import datetime as dt, timedelta

    dated_folder = tmp_path / "2019-12-19 Desktop"
    dated_folder.mkdir()
    (dated_folder / "report-2020-01-01.txt").write_text("x")
    (dated_folder / "no_date_here.txt").write_text("y")

    opts = ScanOptions(extract_date_analysis=True, scan_reference_time=dt.now() + timedelta(days=1))
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0,
              "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    dated_record = next(r for r in records if r["filename"] == "report-2020-01-01.txt")
    da = dated_record["date_analysis"]
    assert da["filename_dates"][0]["value"] == "2020-01-01"
    assert da["folder_path_dates"][0]["segment"] == "2019-12-19 Desktop"
    assert da["folder_path_dates"][0]["dates"][0]["value"] == "2019-12-19"
    assert "flags" not in da  # ordinary dates, well before the reference time -- no anomaly

    undated_record = next(r for r in records if r["filename"] == "no_date_here.txt")
    # Still gets the shared folder_path_dates (attached per-directory), but no filename_dates.
    assert "filename_dates" not in undated_record["date_analysis"]
    assert undated_record["date_analysis"]["folder_path_dates"][0]["segment"] == "2019-12-19 Desktop"


def test_date_analysis_folder_dates_computed_once_per_directory_not_per_file(tmp_path, monkeypatch):
    dated_folder = tmp_path / "2019-12-19 Desktop"
    dated_folder.mkdir()
    for i in range(5):
        (dated_folder / f"file{i}.txt").write_text("x")

    call_count = [0]
    real_find = dateanalysis.find_dates_in_path_segments

    def counting_find(path):
        call_count[0] += 1
        return real_find(path)

    monkeypatch.setattr(dateanalysis, "find_dates_in_path_segments", counting_find)

    opts = ScanOptions(extract_date_analysis=True)
    records = []
    errors = []
    stats = {"files": 0, "bytes": 0, "archives": 0, "archives_not_expanded": 0, "archive_entries": 0,
              "archive_entry_bytes": 0, "archive_entries_skipped_ignore_list": 0, "archive_media_extracted": 0,
              "date_analysis_flagged": 0,
              "skipped_dirs_marker": 0, "skipped_dirs_ignore_list": 0, "skipped_dirs_path_pattern": 0,
              "skipped_files_path_pattern": 0, "self_deferred": False}

    with tempfile.TemporaryDirectory() as scratch:
        walk_root(str(tmp_path), opts, records.append, errors, scratch, stats)

    # One call per directory visited (root + dated_folder), not one per file.
    assert call_count[0] == 2
    assert len(records) == 5


def test_date_analysis_flag_end_to_end(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "old_placeholder.txt").write_text("x")
    old = str(root / "old_placeholder.txt")
    epoch_ts = 0  # 1970-01-01
    os.utime(old, (epoch_ts, epoch_ts))

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
        "--date-analysis",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    record = next(r for r in data["files"] if r["filename"] == "old_placeholder.txt")
    assert "default_epoch_date" in record["date_analysis"]["flags"]
    assert data["summary"]["date_analysis_flagged"] >= 1
    assert data["options"]["date_analysis"] is True


def test_date_analysis_for_archive_entries_uses_internal_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with zipfile.ZipFile(root / "backup.zip", "w") as zf:
        zf.writestr("2019-12-19_export/report-2020-01-01.txt", b"x")

    output_path = tmp_path / "out.json"
    rc = filenav.main([
        str(output_path), "--root", str(root), "--no-media", "--no-timestamp-prefix", "--progress-every", "0",
        "--date-analysis",
    ])
    assert rc == 0

    with open(output_path, encoding="utf-8") as f:
        data = json.load(f)

    entry = next(r for r in data["files"] if r["filename"] == "report-2020-01-01.txt")
    da = entry["date_analysis"]
    assert da["filename_dates"][0]["value"] == "2020-01-01"
    assert da["folder_path_dates"][0]["segment"] == "2019-12-19_export"
