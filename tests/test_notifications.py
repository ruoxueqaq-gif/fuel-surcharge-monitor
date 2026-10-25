import json
import subprocess
from types import SimpleNamespace
import pytest
from src import notifications as box


def test_failure_keeps_pending_and_retry_acknowledges(tmp_path, monkeypatch):
    path = tmp_path / "outbox.json"
    key = box.enqueue(path, "notice", "important change")
    def fail(args, **kwargs):
        if args[1] == "api": return SimpleNamespace(stdout="[[]]")
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(box.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError): box.publish(path, "owner/repo")
    assert key in box.load(path)["pending"]
    def success(args, **kwargs):
        return SimpleNamespace(stdout="[[]]" if args[1] == "api" else "https://github.com/owner/repo/issues/1")
    monkeypatch.setattr(box.subprocess, "run", success)
    assert box.publish(path, "owner/repo") == 1
    assert not box.load(path)["pending"]
    box.enqueue(path, "notice", "important change")
    assert not box.load(path)["pending"]


def test_crash_after_creation_recovers_from_closed_issue(tmp_path, monkeypatch):
    path = tmp_path / "outbox.json"
    key = box.enqueue(path, "notice", "change")
    def existing(args, **kwargs):
        assert args[1] == "api", "must not create duplicate"
        return SimpleNamespace(stdout=json.dumps([[{"body": f"<!-- delivery:{key} -->", "html_url": "https://github.com/o/r/issues/2"}]]))
    monkeypatch.setattr(box.subprocess, "run", existing)
    assert box.publish(path, "owner/repo") == 0
    assert not box.load(path)["pending"]
