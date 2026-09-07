"""Public-layout adapter tests; no credentials, models, or authority are created."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.softwarex import run_public_lifecycle as adapter


def test_published_protocol_matches_frozen_adapter_binding():
    assert adapter.digest(Path(adapter.__file__).with_name("LIVE_CLIENT_PROTOCOL.md")) == adapter.PROTOCOL_SHA256


def test_published_amendment_and_original_support_match_frozen_bindings():
    directory = Path(adapter.__file__).parent
    assert adapter.digest(directory / "LIVE_CLIENT_AMENDMENT_1.md") == adapter.AMENDMENT_SHA256
    assert adapter.digest(directory / "support/tools" / adapter.SUPPORT_HELPER) == adapter.SUPPORT_HELPER_SHA256


def test_inventory_adapter_changes_only_the_root(tmp_path):
    seen = []
    expected = {"identity": "unchanged"}
    def original(root):
        seen.append(root)
        return expected
    call = adapter.inventory_adapter(original, tmp_path.resolve())
    assert call(tmp_path) is expected
    assert seen == [tmp_path / "src"]
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="unexpected inventory"):
        call(other)


@pytest.fixture
def public(tmp_path, monkeypatch):
    (tmp_path / "src/zerorun").mkdir(parents=True)
    rows = []
    for index in range(36):
        path = tmp_path / f"src/zerorun/f{index}.py"
        path.write_bytes(b"# fixture\n")
        rows.append({"path": path.relative_to(tmp_path).as_posix(), "sha256": adapter.digest(path), "bytes": path.stat().st_size})
    manifest = tmp_path / "PUBLIC_RELEASE_MANIFEST.json"
    manifest.write_text(json.dumps({"files": rows, "runtime_modified": False}), encoding="utf-8")
    monkeypatch.setattr(adapter, "PINNED_MANIFEST", adapter.digest(manifest))
    monkeypatch.setattr(adapter, "PROTECTED", {})
    return tmp_path


def test_pinned_inventory_passes(public):
    assert adapter.preflight(public) == public.resolve()


def test_other_import_origin_is_refused(tmp_path):
    expected = tmp_path / "tools/helper.py"
    expected.parent.mkdir()
    expected.touch()
    adapter.validate_imports(tmp_path, {"tools/helper.py": SimpleNamespace(__file__=str(expected))})
    other = tmp_path / "unrelated.py"
    other.touch()
    with pytest.raises(ValueError, match="wrong protected module"):
        adapter.validate_imports(tmp_path, {"tools/helper.py": SimpleNamespace(__file__=str(other))})


@pytest.mark.parametrize("passed", [True, False])
def test_envelope_preserves_original_hash_and_binds_metadata(passed):
    original = {"functional_lifecycle_pass": passed, "agent_stages": []}
    original["evidence_payload_sha256"] = adapter.canonical_hash(original)
    expected = dict(original)
    receipt = adapter.seal_receipt(original, {"passed": True})
    assert original == expected == receipt["original_receipt"]
    assert receipt["functional_lifecycle_pass"] is passed
    amendment = receipt["research_public_layout_adapter"]["pre_model_support_amendment"]
    assert amendment["amendment_sha256"] == adapter.AMENDMENT_SHA256
    assert amendment["helper_sha256"] == adapter.SUPPORT_HELPER_SHA256
    assert amendment["helper_source_commit"] == adapter.SUPPORT_SOURCE_COMMIT
    assert amendment["source_checkout_modified"] is False
    assert receipt["evidence_payload_sha256"] == adapter.canonical_hash(
        {key: value for key, value in receipt.items() if key != "evidence_payload_sha256"})
    changed = dict(original, functional_lifecycle_pass=not passed)
    with pytest.raises(ValueError, match="receipt hash"):
        adapter.seal_receipt(changed, {"passed": True})


def test_postflight_failure_is_retained_not_called_a_pass():
    original = {"functional_lifecycle_pass": True}
    original["evidence_payload_sha256"] = adapter.canonical_hash(original)
    result = adapter.seal_receipt(original, {"passed": False, "error": "source drift"})
    assert result["original_receipt"]["functional_lifecycle_pass"]
    assert result["functional_lifecycle_pass"] is False


def test_linked_root_is_not_silently_resolved(public, tmp_path):
    link = tmp_path / "alias"
    try:
        link.symlink_to(public, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation requires platform privilege")
    with pytest.raises(ValueError, match="non-linked"):
        adapter.preflight(link)


@pytest.mark.parametrize("mutation", ["manifest", "content", "extra", "missing", "helper"])
def test_drift_is_refused(public, monkeypatch, mutation):
    if mutation == "manifest":
        (public / "PUBLIC_RELEASE_MANIFEST.json").write_text("{}", encoding="utf-8")
    elif mutation == "content":
        (public / "src/zerorun/f0.py").write_bytes(b"changed")
    elif mutation == "extra":
        (public / "src/zerorun/extra.py").write_bytes(b"extra")
    elif mutation == "missing":
        (public / "src/zerorun/f0.py").unlink()
    else:
        monkeypatch.setattr(adapter, "PROTECTED", {"missing.py": "0" * 64})
    with pytest.raises(ValueError):
        adapter.preflight(public)


@pytest.fixture
def support_layout(tmp_path, monkeypatch):
    root = tmp_path / "public"
    (root / "tools").mkdir(parents=True)
    directory = tmp_path / "external"
    support_tools = directory / "support/tools"
    support_tools.mkdir(parents=True)
    amendment = directory / "LIVE_CLIENT_AMENDMENT_1.md"
    amendment.write_bytes(b"frozen synthetic amendment\n")
    helper = support_tools / adapter.SUPPORT_HELPER
    helper.write_bytes(b"# synthetic support fixture, never executed\n")
    monkeypatch.setattr(adapter, "AMENDMENT_SHA256", adapter.digest(amendment))
    monkeypatch.setattr(adapter, "SUPPORT_HELPER_SHA256", adapter.digest(helper))
    monkeypatch.delitem(sys.modules, "tools.aggregate_codex_install_evidence", raising=False)
    return root, directory, support_tools, helper


def test_support_preflight_preserves_external_paths(support_layout):
    root, directory, support_tools, helper = support_layout
    assert adapter.support_preflight(root, directory) == (support_tools, helper)


@pytest.mark.parametrize("target", ["amendment", "helper"])
def test_support_bytes_drift_is_refused(support_layout, target):
    root, directory, _, helper = support_layout
    path = directory / "LIVE_CLIENT_AMENDMENT_1.md" if target == "amendment" else helper
    path.write_bytes(b"drift")
    with pytest.raises(ValueError, match="bytes or identity"):
        adapter.support_preflight(root, directory)


def test_support_inside_source_is_refused(support_layout):
    _, directory, _, _ = support_layout
    with pytest.raises(ValueError, match="outside the pinned"):
        adapter.support_preflight(directory, directory)


def test_namespace_support_is_appended_before_import_only_once(support_layout):
    root, _, support_tools, helper = support_layout
    namespace = SimpleNamespace(__file__=None, __path__=[str(root / "tools")])
    module = SimpleNamespace(__file__=str(helper))
    seen = []
    def importer(name):
        seen.append(name)
        if name == "tools":
            return namespace
        assert name == "tools.aggregate_codex_install_evidence"
        assert namespace.__path__ == [str(root / "tools"), str(support_tools)]
        return module
    assert adapter.install_support_namespace(root, support_tools, helper, importer=importer) is module
    assert adapter.install_support_namespace(root, support_tools, helper, importer=importer) is module
    assert seen == ["tools", "tools.aggregate_codex_install_evidence"] * 2


@pytest.mark.parametrize("shadow", ["source_helper", "source_init", "support_init"])
def test_shadow_modules_fail_before_any_import(support_layout, shadow):
    root, _, support_tools, helper = support_layout
    paths = {"source_helper": root / "tools" / adapter.SUPPORT_HELPER,
             "source_init": root / "tools/__init__.py",
             "support_init": support_tools / "__init__.py"}
    paths[shadow].write_bytes(b"raise RuntimeError('must not execute')\n")
    def importer(name):
        pytest.fail("no import may occur after detecting shadow: " + name)
    with pytest.raises(ValueError, match="shadows"):
        adapter.install_support_namespace(root, support_tools, helper, importer=importer)


@pytest.mark.parametrize("kind", ["unrelated_path", "regular_package", "wrong_origin", "cached_wrong_origin"])
def test_namespace_and_support_origin_fail_closed(support_layout, monkeypatch, kind):
    root, directory, support_tools, helper = support_layout
    other = directory / "unrelated.py"
    other.write_bytes(helper.read_bytes())
    namespace = SimpleNamespace(__file__=None, __path__=[str(root / "tools")])
    module = SimpleNamespace(__file__=str(other if kind == "wrong_origin" else helper))
    if kind == "unrelated_path":
        namespace.__path__.append(str(directory))
    elif kind == "regular_package":
        namespace.__file__ = str(other)
    elif kind == "cached_wrong_origin":
        monkeypatch.setitem(sys.modules, "tools.aggregate_codex_install_evidence", SimpleNamespace(__file__=str(other)))
    def importer(name):
        if name == "tools":
            return namespace
        if kind != "wrong_origin":
            pytest.fail("helper must not import after invalid namespace or cached origin")
        return module
    with pytest.raises(ValueError):
        adapter.install_support_namespace(root, support_tools, helper, importer=importer)


def test_linked_support_helper_is_refused(support_layout):
    root, directory, _, helper = support_layout
    target = directory / "actual.py"
    target.write_bytes(helper.read_bytes())
    helper.unlink()
    try:
        helper.symlink_to(target)
    except OSError:
        pytest.skip("file symlink creation requires platform privilege")
    with pytest.raises(ValueError, match="non-linked"):
        adapter.support_preflight(root, directory)
