"""A real tiny Git repository checks archive portability, without publication."""
import io
import shutil
import subprocess
import tarfile

import pytest

from research.softwarex import build_public_release as release


def test_exported_text_representation_is_independent_of_local_git_defaults(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git is required for the publication builder")
    repo = tmp_path / "inert-repository"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)

    git("init")
    git("config", "core.autocrlf", "false")
    git("config", "core.eol", "lf")
    (repo / ".gitattributes").write_bytes(b"sample.py text\n")
    (repo / "sample.py").write_bytes(b"first = 1\nsecond = 2\n")
    git("add", ".gitattributes", "sample.py")
    git("-c", "user.name=Offline publication fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--no-verify", "-m", "Inert newline fixture")
    commit = git("rev-parse", "HEAD").decode().strip()
    raw_blob = git("show", commit + ":sample.py")
    first = release.archive_source(commit, ["sample.py"], root=repo)
    git("config", "core.autocrlf", "true")
    git("config", "core.eol", "crlf")
    second = release.archive_source(commit, ["sample.py"], root=repo)
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first)) as archive:
        exported = archive.extractfile("sample.py").read()
    assert raw_blob == b"first = 1\nsecond = 2\n"
    assert exported == b"first = 1\r\nsecond = 2\r\n"
