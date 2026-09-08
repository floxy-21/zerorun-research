"""Offline source reconstruction boundary tests; not application evidence."""
import io
from pathlib import Path
import tarfile

import pytest

from research.softwarex.agent_application_053 import prepare_consumers as p
from research.softwarex.agent_application_053 import validation as v


def archive(path, entries):
    with tarfile.open(path, "w:gz") as output:
        for name, raw, kind in entries:
            item = tarfile.TarInfo(name); item.type = kind
            if kind == tarfile.REGTYPE:
                item.size = len(raw)
                output.addfile(item, io.BytesIO(raw))
            else:
                item.linkname = raw.decode()
                output.addfile(item)


@pytest.mark.parametrize("bad", ["/root/a", "root/../a", "root/.git/config", "other/a", "root/a"])
def test_archive_rejects_escape_control_mixed_prefix_and_duplicates(tmp_path, bad):
    path = tmp_path / "source.tar.gz"
    archive(path, [("root/a", b"one", tarfile.REGTYPE), (bad, b"two", tarfile.REGTYPE)])
    with pytest.raises(ValueError):
        p.archive_files(path)


def test_archive_preserves_exact_bytes_and_rejects_links(tmp_path):
    path = tmp_path / "source.tar.gz"
    archive(path, [("root/module.py", b"x\r\ny\x00", tarfile.REGTYPE)])
    assert p.archive_files(path) == {"module.py": b"x\r\ny\x00"}
    linked = tmp_path / "link.tar.gz"
    archive(linked, [("root/link", b"target", tarfile.SYMTYPE)])
    with pytest.raises(ValueError):
        p.archive_files(linked)


def test_restoration_refuses_changed_protected_test_before_writing(tmp_path, monkeypatch):
    from research.softwarex.agent_application_053.test_consumer import qualification
    q = qualification()
    root = tmp_path / "workspace"; root.mkdir(); (root / ".git").mkdir(); (root / "tests").mkdir()
    values = {".zerorun.json": b"manifest", "module.py": b"repaired", "tests/test_module.py": b"changed test"}
    for name, raw in values.items():
        (root / name).write_bytes(raw)
    # The concrete Linux-root check is separately covered by qualification tests.
    q["root"] = root.as_posix()
    monkeypatch.setattr(v, "validate_qualification", lambda value: None)
    directory = tmp_path / "record-only/cases" / q["case_id"]; directory.mkdir(parents=True)
    p.save(directory / "qualification.json", q)
    with pytest.raises(ValueError, match="restoration does not begin"):
        p.restore(tmp_path, q["case_id"])
    assert (root / "module.py").read_bytes() == b"repaired"
