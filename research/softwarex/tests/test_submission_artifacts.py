"""Offline fixture-only archive safety checks; no real manuscript is built."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from research.softwarex import build_submission_artifacts as builder


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def put_json(path, value):
    put(path, (json.dumps(value) + "\n").encode())


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    here = tmp_path / "research/softwarex"
    monkeypatch.setattr(builder, "HERE", here)
    source = {
        "main.tex": (b"\\documentclass{elsarticle}\n\\begin{document}\nText \\cite{one}.\n"
                     b"\\bibliographystyle{elsarticle-num}\n\\bibliography{references}\n\\end{document}\n"),
        "references.bib": b"@article{one, title={A fixture}}\n",
        "main.bbl": b"\\begin{thebibliography}{1}\n\\bibitem{one} A fixture.\n\\end{thebibliography}\n",
        "elsarticle.cls": b"Fixture class, not a real compiler input.\n",
        "elsarticle-num.bst": b"Fixture bibliography style.\n",
        "STYLE_SOURCE_NOTICE.txt": b"Fixture notice.\n",
    }
    for name, raw in source.items():
        put(here / "paper" / name, raw)
    pdf = b"%PDF-1.4\nNot a real PDF; isolated unit fixture only.\n"
    put(tmp_path / "output/pdf/zerorun-softwarex.pdf", pdf)
    evidence = {"preview": False, "replication": {"completed": True},
                "main_tex_sha256": builder.digest(source["main.tex"]),
                "bibliography_sha256": builder.digest(source["references.bib"])}
    put_json(here / "generated/paper-evidence.json", evidence)
    qa = {"pdf_sha256": builder.digest(pdf), "all_pages_visually_reviewed": True,
          "unresolved_references": False, "word_limit_pass": True,
          "compilation_source_sha256": {name: builder.digest(raw) for name, raw in source.items()}}
    put_json(here / "generated/pdf-review.json", qa)
    release = tmp_path / "release"
    public = {"output/pdf/zerorun-softwarex.pdf": pdf, "README.md": b"Offline fixture release.\n",
              **{"research/softwarex/paper/" + name: raw for name, raw in source.items()}}
    refresh_release(release, public)
    return {"root": tmp_path, "here": here, "source": source, "qa": qa,
            "release": release, "public": public}


def refresh_release(release, public):
    for name, raw in public.items():
        put(release / name, raw)
    put_json(release / "PUBLIC_RELEASE_MANIFEST.json", {"files": [
        {"path": name, "bytes": len(raw), "sha256": builder.digest(raw)}
        for name, raw in sorted(public.items())]})


def assert_no_archives(fixture):
    assert not (fixture["root"] / "output/submission/SoftwareX_source.zip").exists()
    assert not (fixture["root"] / "output/submission/ZeroRun_SoftwareX_reviewer.zip").exists()
    assert not (fixture["here"] / "generated/artifact-build.json").exists()
    assert not list(fixture["root"].rglob("*.building"))


def raw_zip(path, payloads, attrs=None):
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in payloads.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            if attrs and name in attrs:
                info.external_attr = attrs[name]
            archive.writestr(info, raw)


def test_valid_build_binds_both_archives_to_reviewed_sources(fixture):
    receipt = builder.build(fixture["release"])
    assert receipt["completed"] is True
    assert receipt["compilation_source_sha256"] == fixture["qa"]["compilation_source_sha256"]
    assert receipt["journal_submitted"] is False and receipt["payment_made"] is False
    assert receipt["pdf_review_sha256"] == builder.digest((fixture["here"] / "generated/pdf-review.json").read_bytes())
    for key in ("source_archive", "reviewer_archive"):
        path = fixture["root"] / receipt[key]["path"]
        assert builder.verify(path) == receipt[key]
    with zipfile.ZipFile(fixture["root"] / receipt["source_archive"]["path"]) as archive:
        assert set(archive.namelist()) == set(builder.SOURCE_FILES) | {builder.ARCHIVE_MANIFEST}
    assert not list(fixture["root"].rglob("*.building"))


@pytest.mark.parametrize("field,value", [
    ("compilation_source_sha256", None), ("compilation_source_sha256", {"main.tex": "0" * 64}),
    ("pdf_sha256", "0" * 64), ("all_pages_visually_reviewed", False),
    ("unresolved_references", True), ("word_limit_pass", False),
])
def test_missing_or_stale_pdf_review_never_creates_archive(fixture, field, value):
    fixture["qa"][field] = value
    put_json(fixture["here"] / "generated/pdf-review.json", fixture["qa"])
    with pytest.raises(ValueError):
        builder.build(fixture["release"])
    assert_no_archives(fixture)


@pytest.mark.parametrize("name", builder.SOURCE_FILES)
def test_every_compilation_input_is_bound_to_pdf_review(fixture, name):
    path = fixture["here"] / "paper" / name
    put(path, path.read_bytes() + b"\nChanged after review.\n")
    with pytest.raises(ValueError, match="compilation-source binding"):
        builder.build(fixture["release"])
    assert_no_archives(fixture)


@pytest.mark.parametrize("name", ["output/pdf/zerorun-softwarex.pdf"] +
                         ["research/softwarex/paper/" + name for name in builder.SOURCE_FILES])
def test_stale_public_pdf_or_source_does_not_publish_first_archive(fixture, name):
    fixture["public"][name] += b"stale"
    refresh_release(fixture["release"], fixture["public"])
    with pytest.raises(ValueError, match="public release"):
        builder.build(fixture["release"])
    assert_no_archives(fixture)


@pytest.mark.parametrize("name", sorted(builder.PRIOR_ARTIFACTS))
def test_recursive_or_old_submission_in_release_is_refused(fixture, name):
    fixture["public"][name] = b"An earlier artifact."
    refresh_release(fixture["release"], fixture["public"])
    with pytest.raises(ValueError, match="already contains submission artifacts"):
        builder.build(fixture["release"])
    assert_no_archives(fixture)


@pytest.mark.parametrize("existing", ["output/submission/SoftwareX_source.zip",
                                     "output/submission/ZeroRun_SoftwareX_reviewer.zip",
                                     "research/softwarex/generated/artifact-build.json"])
def test_existing_final_is_never_overwritten_and_no_other_final_created(fixture, existing):
    path = fixture["root"] / existing
    put(path, b"User-owned old artifact")
    with pytest.raises(ValueError, match="already exists"):
        builder.build(fixture["release"])
    assert path.read_bytes() == b"User-owned old artifact"
    for name in builder.PRIOR_ARTIFACTS - {existing}:
        assert not (fixture["root"] / name).exists()
    assert not list(fixture["root"].rglob("*.building"))


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "a//b.txt", "a/./b.txt",
                                  "a\\b.txt", "C:secret.txt", ".git/config", "private/key"])
def test_unsafe_or_noncanonical_archive_names_are_refused(fixture, name):
    with pytest.raises(ValueError):
        builder.write_archive(fixture["root"] / "bad.zip", {name: b"fixture"}, "reviewer-software-and-evidence")
    assert not (fixture["root"] / "bad.zip").exists()
    assert not list(fixture["root"].rglob("*.building"))


def test_secret_shaped_fixture_is_refused_before_staging(fixture):
    dummy = b"gh" + b"p_" + b"A" * 36
    with pytest.raises(ValueError, match="possible secret"):
        builder.write_archive(fixture["root"] / "bad.zip", {"fixture.txt": dummy}, "reviewer-software-and-evidence")
    assert not list(fixture["root"].rglob("*.building"))


@pytest.mark.parametrize("payloads,kind", [
    ({builder.ARCHIVE_MANIFEST: b"{}"}, "reviewer-software-and-evidence"),
    ({"bad.txt": None}, "reviewer-software-and-evidence"),
    ({"main.tex": b"missing other five"}, "flat-editable-manuscript"),
    ({"one.txt": b"fixture"}, []),
])
def test_malformed_archive_request_fails_closed(fixture, payloads, kind):
    with pytest.raises(ValueError):
        builder.write_archive(fixture["root"] / "bad.zip", payloads, kind)
    assert not list(fixture["root"].rglob("*.building"))


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}'])
def test_strict_json_refuses_ambiguous_or_nonfinite_data(raw):
    with pytest.raises(ValueError):
        builder.strict_json(raw)


@pytest.mark.parametrize("mutation", ["schema", "kind-list", "bool-bytes", "negative-bytes", "bad-hash",
                                      "duplicate", "extra-field", "reserved", "path-traversal"])
def test_manifest_schema_is_strict(mutation):
    payloads = builder.validated_payloads({"one.txt": b"fixture"}, "reviewer-software-and-evidence")
    manifest = json.loads(payloads[builder.ARCHIVE_MANIFEST])
    row = manifest["files"][0]
    if mutation == "schema":
        manifest["schema"] = "other"
    elif mutation == "kind-list":
        manifest["kind"] = []
    elif mutation == "bool-bytes":
        row["bytes"] = True
    elif mutation == "negative-bytes":
        row["bytes"] = -1
    elif mutation == "bad-hash":
        row["sha256"] = "unknown"
    elif mutation == "duplicate":
        manifest["files"].append(deepcopy(row))
    elif mutation == "extra-field":
        manifest["untrusted"] = True
    elif mutation == "reserved":
        row["path"] = builder.ARCHIVE_MANIFEST
    else:
        row["path"] = "../outside"
    with pytest.raises(ValueError):
        builder.load_manifest_bytes(json.dumps(manifest).encode())


@pytest.mark.parametrize("mode", ["symlink", "directory", "wrong-hash", "unlisted", "duplicate"])
def test_zip_verification_refuses_unsafe_inventory(fixture, mode):
    payloads = builder.validated_payloads({"one.txt": b"fixture"}, "reviewer-software-and-evidence")
    attrs = {}
    if mode == "symlink":
        attrs["one.txt"] = (stat.S_IFLNK | 0o777) << 16
    elif mode == "directory":
        attrs["one.txt"] = (stat.S_IFDIR | 0o755) << 16
    elif mode == "wrong-hash":
        payloads["one.txt"] = b"changed"
    elif mode == "unlisted":
        payloads["second.txt"] = b"extra"
    path = fixture["root"] / "unsafe.zip"
    raw_zip(path, payloads, attrs)
    if mode == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("one.txt", b"fixture")
    with pytest.raises(ValueError):
        builder.verify(path)


def test_zip_expansion_bound_checked_before_payload_read(fixture, monkeypatch):
    payloads = builder.validated_payloads({"one.txt": b"fixture"}, "reviewer-software-and-evidence")
    path = fixture["root"] / "bounded.zip"
    raw_zip(path, payloads)
    monkeypatch.setattr(builder, "MAX_TOTAL_BYTES", 1)
    with pytest.raises(ValueError, match="expansion bound"):
        builder.verify(path)


@pytest.mark.parametrize("change", [b"\\input{missing.tex}", b"\\includegraphics{plot.pdf}",
                                   b"\\bibliography{another}", b"\\cite{uncited}"])
def test_flat_source_refuses_missing_inputs_or_bibliography(fixture, change):
    source = dict(fixture["source"])
    source["main.tex"] += change
    with pytest.raises(ValueError):
        builder.validate_flat_source(source)


def test_both_archives_verified_before_any_publication(fixture, monkeypatch):
    real_verify = builder.verify
    seen = []
    def fail_second(path):
        seen.append(path)
        if len(seen) == 2:
            raise ValueError("injected second-stage verification failure")
        return real_verify(path)
    monkeypatch.setattr(builder, "verify", fail_second)
    with pytest.raises(ValueError, match="second-stage"):
        builder.build(fixture["release"])
    assert len(seen) == 2
    assert_no_archives(fixture)
    failure = json.loads(next((fixture["here"] / "generated").glob("artifact-build-failure-*.json")).read_bytes())
    assert failure["completed"] is False and failure["published_outputs_retained"] == []


def test_publication_race_retains_existing_files_and_never_marks_complete(fixture, monkeypatch):
    real_link = os.link
    calls = []
    def race(source, final):
        calls.append(final)
        if len(calls) == 2:
            put(Path(final), b"Another writer's artifact")
        return real_link(source, final)
    monkeypatch.setattr(builder.os, "link", race)
    with pytest.raises(FileExistsError):
        builder.build(fixture["release"])
    source = fixture["root"] / "output/submission/SoftwareX_source.zip"
    assert builder.verify(source)["verified"]
    assert (fixture["root"] / "output/submission/ZeroRun_SoftwareX_reviewer.zip").read_bytes() == b"Another writer's artifact"
    assert not (fixture["here"] / "generated/artifact-build.json").exists()
    assert not list(fixture["root"].rglob("*.building"))
    failure = json.loads(next((fixture["here"] / "generated").glob("artifact-build-failure-*.json")).read_bytes())
    assert failure["completed"] is False
    assert failure["published_outputs_retained"] == ["output/submission/SoftwareX_source.zip"]


def test_cleanup_never_removes_unowned_file(fixture):
    path = fixture["root"] / ".owned.building"
    put(path, b"User-owned data")
    info = path.stat()
    stage = {"path": path, "parent": path.parent.resolve(), "device": info.st_dev, "inode": info.st_ino + 1}
    with pytest.raises(ValueError, match="identity changed"):
        builder.cleanup_stage(stage)
    assert path.read_bytes() == b"User-owned data"


def test_source_cannot_change_after_public_manifest_inspection(fixture, monkeypatch):
    real_inspect = builder.inspect
    def mutate(directory):
        result = real_inspect(directory)
        put(directory / "README.md", b"Changed during build")
        return result
    monkeypatch.setattr(builder, "inspect", mutate)
    with pytest.raises(ValueError, match="changed after inventory"):
        builder.build(fixture["release"])
    assert_no_archives(fixture)
