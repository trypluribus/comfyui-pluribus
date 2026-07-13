import json
import os
import stat
from pathlib import Path

import pytest

from pluribus import storage
from pluribus.invites import read_actions


def test_private_json_write_is_atomic_and_owner_only(tmp_path):
    path = tmp_path / "state" / "invites.json"

    storage.write_private_json(str(path), [{"secret": "accept-link"}])

    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"secret": "accept-link"}
    ]
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_failed_private_json_write_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "invites.json"
    storage.write_private_json(str(path), [{"version": 1}])

    def fail_dump(*_args, **_kwargs):
        raise RuntimeError("simulated serialization failure")

    monkeypatch.setattr(storage.json, "dump", fail_dump)
    with pytest.raises(RuntimeError, match="simulated"):
        storage.write_private_json(str(path), [{"version": 2}])

    assert json.loads(path.read_text(encoding="utf-8")) == [{"version": 1}]
    assert list(tmp_path.glob(".invites.json.*.tmp")) == []


def test_corrupt_invite_file_is_preserved_before_recovery(tmp_path):
    path = tmp_path / "invites.json"
    path.write_text('{"truncated":', encoding="utf-8")

    assert read_actions(str(path)) == []
    assert not path.exists()
    backups = list(tmp_path.glob("invites.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"truncated":'
    if os.name != "nt":
        assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_temp_fallback_is_private_and_distinct_per_install(tmp_path):
    first = storage.fallback_private_data_dir("/opt/comfy/one", str(tmp_path))
    second = storage.fallback_private_data_dir("/opt/comfy/two", str(tmp_path))

    assert first != second
    if os.name != "nt":
        assert stat.S_IMODE(Path(first).stat().st_mode) == 0o700
        assert stat.S_IMODE(Path(second).stat().st_mode) == 0o700


def test_private_json_write_works_without_posix_fchmod(tmp_path, monkeypatch):
    path = tmp_path / "connection.json"
    monkeypatch.delattr(storage.os, "fchmod", raising=False)

    storage.write_private_json(str(path), {"token": "local-only"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"token": "local-only"}
