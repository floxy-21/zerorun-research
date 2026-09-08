"""Verify the exact 0.5.3 integration release without rewriting 0.5.2 evidence.

Runtime computation is compared with the frozen 0.5.1 study source. Only the
reviewed Codex integration, version, neutral MCP descriptions, and RunResult
class documentation differ. Pin values use the public builder's explicit
core.autocrlf=true/core.eol=crlf git-archive representation, never worktree bytes.
This module does not authorize repositories, invoke models, or run Docker.
"""
from __future__ import annotations
import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
from email.parser import Parser
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
import tempfile
import zipfile

if __package__:
    from . import current_runtime as historical
else:
    # Preserve direct-file CLI usage while loading only the exact sibling.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("zerorun_current_runtime_052_helper", Path(__file__).with_name("current_runtime.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("the historical current-runtime helper is unavailable")
    historical = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(historical)

require = historical.require
canonical = historical.canonical
digest = historical.digest
strict_json = historical.strict_json
regular = historical.regular
rows = historical.rows
stream = historical.stream
decoded = historical.decoded
invoke = historical.invoke
junit_counts = historical.junit_counts
execution_environment = historical.execution_environment
SMOKE = historical.SMOKE
TEST_SCRIPT = historical.TEST_SCRIPT

HISTORICAL_CORE = historical.HISTORICAL_CORE
HISTORICAL_PREFIX = historical.HISTORICAL_PREFIX
CURRENT_CORE = 'e5194d340a09bccb667d3021a6a4a9a9a054123b'
VERSION = "0.5.3"
SCHEMA = "zerorun.softwarex-current-runtime-0.5.3.v1"
SELF = "research/softwarex/five_hour_review/current_runtime_053.py"
WHEEL = "output/packages/zerorun-softwarex/zerorun-0.5.3-py3-none-any.whl"
REQUIRED_TESTS = historical.REQUIRED_TESTS
ALLOWED_CHANGED = {"__init__.py", "mcp.py", "codex.py", "model.py"}

HISTORICAL_FILES = {
  "__init__.py": {
    "bytes": 56,
    "sha256": "1af5000ed9bbd50e05657d51f12f0a36f415ab8370d93e7e85a2a718c16b8187"
  },
  "__main__.py": {
    "bytes": 83,
    "sha256": "d220d88dd9a25cd0629c5176dd6532a2db1da40eb014a8e80796b0ddeb134918"
  },
  "api.py": {
    "bytes": 2482,
    "sha256": "49e7a5a35333ed2c5ae7b695491f307ad21619cbafcd523b773a04d84518ea25"
  },
  "bounded_json.py": {
    "bytes": 6620,
    "sha256": "923adb40098249908adf509eec06db1f876b1b21c25e46c97a1c8490e40df4ff"
  },
  "cli.py": {
    "bytes": 17782,
    "sha256": "7d8a6f52bf4c97394eb9400f8dc593d7d25cc1119347e3e4898395155807730a"
  },
  "codex.py": {
    "bytes": 30489,
    "sha256": "6b2c5fe7a33e6df8da595b846941a7059ac26017d4fccd606e77a4ad4dea1f1d"
  },
  "fingerprint.py": {
    "bytes": 52838,
    "sha256": "63c6f8a7db62daaed0712c5c7b1c98ead4c5d50fef26f35d4d793dc70b2f00b8"
  },
  "hermetic.py": {
    "bytes": 20351,
    "sha256": "de63a77cbf7a342a23f71604cb6abdd9314ae02ec9df358527ab75e5ee6a5f38"
  },
  "hermetic_batch.py": {
    "bytes": 2694,
    "sha256": "b7bdce8db63ca12126f92119549c7920754544e2f2f50739e7ea725f14d3cb12"
  },
  "hermetic_batch_key.py": {
    "bytes": 8384,
    "sha256": "f50e4e0575a2dc6d415a090caba1e66ec72468f016b80537395028dc964d0867"
  },
  "hermetic_key.py": {
    "bytes": 35885,
    "sha256": "803d7dca8b0fffc9e5ba5ebb18830742d1225e0aa0de906d5cb6f1db76dd81ad"
  },
  "hermetic_store.py": {
    "bytes": 17674,
    "sha256": "4ed4fcb41cfb5191e55e94380294470b0b79aad542fb97e3a5cf5a3e1f842d42"
  },
  "manifest.py": {
    "bytes": 10734,
    "sha256": "e1a276cbb710b458f85d2143f29c489f5fde70adcc06d184f39c045c42a93314"
  },
  "mcp.py": {
    "bytes": 47962,
    "sha256": "62c5634e467cf1d494e04e2b2c535f30b7431ab4d133f979c1105595de1432d1"
  },
  "model.py": {
    "bytes": 8275,
    "sha256": "0ff7ac3239b3436fb78b2085a0e6bf0553f27dee33ec5dc1535858396f578695"
  },
  "normalization.py": {
    "bytes": 3296,
    "sha256": "7655bb313b8a2163f8796efe48a6fed13c9b81acc7d06c70f6e3294a1fca4db8"
  },
  "oci.py": {
    "bytes": 61775,
    "sha256": "7a64c92185e2252df4e54a9b9de9ec96647b7005c0684d16fe42a767e3493e12"
  },
  "path_safety.py": {
    "bytes": 7899,
    "sha256": "0e46519f64228b69f5aaea7fcbd16e9f9d60d025f1c136e02bd238eb5229a8c1"
  },
  "pilot.py": {
    "bytes": 6231,
    "sha256": "ebd3e4622891c32d55f6753a4c0bf973bd8a286c84447bf48a60a9fd336daa76"
  },
  "pytest_closure.py": {
    "bytes": 70910,
    "sha256": "91bafd7c725db8f1bd526d52dbca67c3488f315d15600fa94f25f792883d15be"
  },
  "pytest_collection_contract.py": {
    "bytes": 9253,
    "sha256": "2c3e4129d06d6039c1fbf1155e999d52861d2255f58587d7872d15f33364a351"
  },
  "pytest_node_cache.py": {
    "bytes": 17239,
    "sha256": "4263dd72563a9c0cc18770fb60ea1229246cf6936b45ee3d80d0bb833c50a7d2"
  },
  "pytest_node_identity.py": {
    "bytes": 10645,
    "sha256": "f508d4c1a25ebd644ffb5f049bade2030f1d94151b8c56ebd28a7163ebf6f9a2"
  },
  "pytest_node_key.py": {
    "bytes": 15943,
    "sha256": "20b75f5181c562ea4faf4662683f1e39d7ddaf07b12009866d8f4cfc6ff86cf4"
  },
  "pytest_prepare_cli.py": {
    "bytes": 6748,
    "sha256": "c656d0e5cd437ab5ba5f62f983adbdec5457abc5e5bb36d36850a19f97f5efb3"
  },
  "pytest_profile.py": {
    "bytes": 33421,
    "sha256": "ae18b08a7b8f3596dc8dace81a3134f9add88d344b5f2941bf3cfdebb8a751db"
  },
  "pytest_qualify.py": {
    "bytes": 210988,
    "sha256": "73a66af6bea79550e218b28155903e9f0cce02652599143db0ad9ad7c3315010"
  },
  "pytest_review.py": {
    "bytes": 13122,
    "sha256": "41107bef44bb0b777a2f9a8525f4dc0b271e733ed72e3e7220b2fcbe1d87a9d6"
  },
  "pytest_review_cli.py": {
    "bytes": 4088,
    "sha256": "0a83fc5a5c1349c84aa18a156989c942f1f83a0c062d168825bf3afb5b3fa2e0"
  },
  "pytest_runtime.py": {
    "bytes": 174340,
    "sha256": "ff8375136ea79949fc65f0f845ac9d0e9a567e7a40288b42192a76df6e03f626"
  },
  "pytest_setup.py": {
    "bytes": 25909,
    "sha256": "2966f27d7a31d530b13ff2f5ef09d6c22dbf36144dba713d1a4a6360fb3adb80"
  },
  "runner.py": {
    "bytes": 25978,
    "sha256": "a37cab0c195c524a6ddad763fa286e2ddc9137b297f3fedece00f212bf1a94c5"
  },
  "source_uncertainty.py": {
    "bytes": 1236,
    "sha256": "af4d4095fad81b7922f8b54a9e804ae6d2294fb1862bf07e83a60eb4084a9d6c"
  },
  "store.py": {
    "bytes": 36250,
    "sha256": "36a1e2a776b82d002643d4ad4ac97659ccb484fa83e7a3d929f07160f347b57e"
  },
  "trust.py": {
    "bytes": 35046,
    "sha256": "a26d1e2d13011799318f21252c01708de135471c441716ea2bc86202b17d8247"
  },
  "workspace.py": {
    "bytes": 19233,
    "sha256": "a91d6b55b9627b4c6b45d84381c4cc7bc69f9f7413289e9c853e0e01582ef2d3"
  }
}

CURRENT_FILES = {
  "__init__.py": {
    "bytes": 56,
    "sha256": "728048b1b2007ee0a0751c2356e468f6a3268ce776c098454cc6a30d21bcdc34"
  },
  "__main__.py": {
    "bytes": 83,
    "sha256": "d220d88dd9a25cd0629c5176dd6532a2db1da40eb014a8e80796b0ddeb134918"
  },
  "api.py": {
    "bytes": 2482,
    "sha256": "49e7a5a35333ed2c5ae7b695491f307ad21619cbafcd523b773a04d84518ea25"
  },
  "bounded_json.py": {
    "bytes": 6620,
    "sha256": "923adb40098249908adf509eec06db1f876b1b21c25e46c97a1c8490e40df4ff"
  },
  "cli.py": {
    "bytes": 17782,
    "sha256": "7d8a6f52bf4c97394eb9400f8dc593d7d25cc1119347e3e4898395155807730a"
  },
  "codex.py": {
    "bytes": 38591,
    "sha256": "3a875a0db0d0a146bc0032ee1a48bb3e5720f44ba0fd99524d6a3e8d5c84639b"
  },
  "fingerprint.py": {
    "bytes": 52838,
    "sha256": "63c6f8a7db62daaed0712c5c7b1c98ead4c5d50fef26f35d4d793dc70b2f00b8"
  },
  "hermetic.py": {
    "bytes": 20351,
    "sha256": "de63a77cbf7a342a23f71604cb6abdd9314ae02ec9df358527ab75e5ee6a5f38"
  },
  "hermetic_batch.py": {
    "bytes": 2694,
    "sha256": "b7bdce8db63ca12126f92119549c7920754544e2f2f50739e7ea725f14d3cb12"
  },
  "hermetic_batch_key.py": {
    "bytes": 8384,
    "sha256": "f50e4e0575a2dc6d415a090caba1e66ec72468f016b80537395028dc964d0867"
  },
  "hermetic_key.py": {
    "bytes": 35885,
    "sha256": "803d7dca8b0fffc9e5ba5ebb18830742d1225e0aa0de906d5cb6f1db76dd81ad"
  },
  "hermetic_store.py": {
    "bytes": 17674,
    "sha256": "4ed4fcb41cfb5191e55e94380294470b0b79aad542fb97e3a5cf5a3e1f842d42"
  },
  "manifest.py": {
    "bytes": 10734,
    "sha256": "e1a276cbb710b458f85d2143f29c489f5fde70adcc06d184f39c045c42a93314"
  },
  "mcp.py": {
    "bytes": 50523,
    "sha256": "cf32d47dc40e914c6f2e411548dca4525005b0928821c035018fdc76b0cd63f4"
  },
  "model.py": {
    "bytes": 8758,
    "sha256": "e8b88ba2fa8cb77ee16a6659c6b119324b7ae4f92e11734bf6af99556e503159"
  },
  "normalization.py": {
    "bytes": 3296,
    "sha256": "7655bb313b8a2163f8796efe48a6fed13c9b81acc7d06c70f6e3294a1fca4db8"
  },
  "oci.py": {
    "bytes": 61775,
    "sha256": "7a64c92185e2252df4e54a9b9de9ec96647b7005c0684d16fe42a767e3493e12"
  },
  "path_safety.py": {
    "bytes": 7899,
    "sha256": "0e46519f64228b69f5aaea7fcbd16e9f9d60d025f1c136e02bd238eb5229a8c1"
  },
  "pilot.py": {
    "bytes": 6231,
    "sha256": "ebd3e4622891c32d55f6753a4c0bf973bd8a286c84447bf48a60a9fd336daa76"
  },
  "pytest_closure.py": {
    "bytes": 70910,
    "sha256": "91bafd7c725db8f1bd526d52dbca67c3488f315d15600fa94f25f792883d15be"
  },
  "pytest_collection_contract.py": {
    "bytes": 9253,
    "sha256": "2c3e4129d06d6039c1fbf1155e999d52861d2255f58587d7872d15f33364a351"
  },
  "pytest_node_cache.py": {
    "bytes": 17239,
    "sha256": "4263dd72563a9c0cc18770fb60ea1229246cf6936b45ee3d80d0bb833c50a7d2"
  },
  "pytest_node_identity.py": {
    "bytes": 10645,
    "sha256": "f508d4c1a25ebd644ffb5f049bade2030f1d94151b8c56ebd28a7163ebf6f9a2"
  },
  "pytest_node_key.py": {
    "bytes": 15943,
    "sha256": "20b75f5181c562ea4faf4662683f1e39d7ddaf07b12009866d8f4cfc6ff86cf4"
  },
  "pytest_prepare_cli.py": {
    "bytes": 6748,
    "sha256": "c656d0e5cd437ab5ba5f62f983adbdec5457abc5e5bb36d36850a19f97f5efb3"
  },
  "pytest_profile.py": {
    "bytes": 33421,
    "sha256": "ae18b08a7b8f3596dc8dace81a3134f9add88d344b5f2941bf3cfdebb8a751db"
  },
  "pytest_qualify.py": {
    "bytes": 210988,
    "sha256": "73a66af6bea79550e218b28155903e9f0cce02652599143db0ad9ad7c3315010"
  },
  "pytest_review.py": {
    "bytes": 13122,
    "sha256": "41107bef44bb0b777a2f9a8525f4dc0b271e733ed72e3e7220b2fcbe1d87a9d6"
  },
  "pytest_review_cli.py": {
    "bytes": 4088,
    "sha256": "0a83fc5a5c1349c84aa18a156989c942f1f83a0c062d168825bf3afb5b3fa2e0"
  },
  "pytest_runtime.py": {
    "bytes": 174340,
    "sha256": "ff8375136ea79949fc65f0f845ac9d0e9a567e7a40288b42192a76df6e03f626"
  },
  "pytest_setup.py": {
    "bytes": 25909,
    "sha256": "2966f27d7a31d530b13ff2f5ef09d6c22dbf36144dba713d1a4a6360fb3adb80"
  },
  "runner.py": {
    "bytes": 25978,
    "sha256": "a37cab0c195c524a6ddad763fa286e2ddc9137b297f3fedece00f212bf1a94c5"
  },
  "source_uncertainty.py": {
    "bytes": 1236,
    "sha256": "af4d4095fad81b7922f8b54a9e804ae6d2294fb1862bf07e83a60eb4084a9d6c"
  },
  "store.py": {
    "bytes": 36250,
    "sha256": "36a1e2a776b82d002643d4ad4ac97659ccb484fa83e7a3d929f07160f347b57e"
  },
  "trust.py": {
    "bytes": 35046,
    "sha256": "a26d1e2d13011799318f21252c01708de135471c441716ea2bc86202b17d8247"
  },
  "workspace.py": {
    "bytes": 19233,
    "sha256": "a91d6b55b9627b4c6b45d84381c4cc7bc69f9f7413289e9c853e0e01582ef2d3"
  }
}

REQUIRED_SUPPORT = {
  "tests/test_mcp.py": {
    "bytes": 33198,
    "sha256": "9af96225c46f51cd49c3699074e12775bc09d62f11f0b36d7badaaf515937180"
  },
  "tests/test_codex_integration.py": {
    "bytes": 38471,
    "sha256": "24d40a8fab19f4637681c45d66aa42a8a2911cd899f5bb92d87f0096ae04c434"
  },
  "tests/test_mcp_discovery_semantics.py": {
    "bytes": 8752,
    "sha256": "78e91163ac107dac617476e56a20f2b7eecb8057771503f661a200be55575c8f"
  },
  "docs/zerorun-SKILL.md": {
    "bytes": 3359,
    "sha256": "21ae21cf9c648f223ae22425c18b34e7ad5b551777145d826ddd57baa91a3967"
  }
}

SUPPORT_ORIGINS = {
  "tests/test_mcp.py": "git:e5194d340a09bccb667d3021a6a4a9a9a054123b:tests/test_mcp.py",
  "tests/test_codex_integration.py": "git:e5194d340a09bccb667d3021a6a4a9a9a054123b:tests/test_codex_integration.py; one bundled-skill path adapted to research docs",
  "tests/test_mcp_discovery_semantics.py": "git:e5194d340a09bccb667d3021a6a4a9a9a054123b:tests/test_mcp_discovery_semantics.py",
  "docs/zerorun-SKILL.md": "git:e5194d340a09bccb667d3021a6a4a9a9a054123b:.agents/skills/zerorun/SKILL.md"
}

def _bound_files(files, expected, label):
    require(set(files) == set(expected), label + " filename denominator differs")
    for name, raw in files.items():
        require({"bytes": len(raw), "sha256": digest(raw)} == expected[name],
                label + " exact exported bytes differ: " + name)


def _literal_assignment(raw, name):
    matches = [n for n in ast.parse(raw.decode("utf-8")).body
               if isinstance(n, ast.Assign) and len(n.targets) == 1
               and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name]
    require(len(matches) == 1, "single canonical skill assignment required")
    value = ast.literal_eval(matches[0].value)
    require(isinstance(value, str), "canonical skill must be a string literal")
    return value


def validate_model_documentation(old, current):
    before, after = ast.parse(old.decode("utf-8")), ast.parse(current.decode("utf-8"))
    def result_class(tree):
        candidates = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RunResult"]
        require(len(candidates) == 1, "single RunResult class required")
        return candidates[0]
    old_class, new_class = result_class(before), result_class(after)
    require(ast.get_docstring(old_class) is None and bool(ast.get_docstring(new_class)),
            "only the new RunResult class docstring is permitted")
    require(isinstance(new_class.body[0], ast.Expr) and isinstance(new_class.body[0].value, ast.Constant)
            and isinstance(new_class.body[0].value.value, str), "RunResult docstring structure differs")
    del new_class.body[0]
    require(ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False),
            "model computation changed beyond RunResult documentation")


def validate_integration_changes(old, current):
    require(len(old) == len(current) == 36 and set(old) == set(current), "runtime denominator must remain 36")
    _bound_files(old, HISTORICAL_FILES, "historical runtime")
    _bound_files(current, CURRENT_FILES, "current integration runtime")
    changed = {name for name in old if old[name] != current[name]}
    require(changed == ALLOWED_CHANGED, "unexpected integration runtime changes")
    require(old["__init__.py"].count(b'"0.5.1"') == 1
            and current["__init__.py"] == old["__init__.py"].replace(b'"0.5.1"', b'"0.5.3"'),
            "version module changed beyond the explicit release string")
    # Reuse the strict historical metadata comparison on a derived byte map.
    # No module globals, historical sources, or historical receipts are modified.
    metadata_comparison = dict(current)
    metadata_comparison.update({name: old[name] for name in ("codex.py", "model.py")})
    metadata_comparison["__init__.py"] = old["__init__.py"].replace(b'"0.5.1"', b'"0.5.2"')
    historical.validate_metadata_only(old, metadata_comparison)
    validate_model_documentation(old["model.py"], current["model.py"])
    require(_literal_assignment(current["codex.py"], "_PREVIOUS_SKILL")
            == _literal_assignment(old["codex.py"], "_SKILL"), "managed-skill migration lost the exact historical payload")
    return sorted(changed)


def snapshot(release):
    release = Path(release)
    manifest = strict_json(regular(release / "PUBLIC_RELEASE_MANIFEST.json"))
    commit = manifest.get("current_core_commit")
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
            and commit == CURRENT_CORE and manifest.get("historical_core_commit") == HISTORICAL_CORE
            and manifest.get("current_version") == VERSION, "current/historical release identity absent")
    current_root = release / "src/zerorun"
    old_root = release / HISTORICAL_PREFIX / "src/zerorun"
    current_paths = sorted(current_root.rglob("*.py"))
    old_paths = sorted(old_root.rglob("*.py"))
    require(len(current_paths) == len(old_paths) == 36
            and all(p.parent == current_root for p in current_paths)
            and all(p.parent == old_root for p in old_paths), "runtime inventory must remain exactly 36 flat modules")
    current = {p.name: regular(p) for p in current_paths}
    old = {p.name: regular(p) for p in old_paths}
    changed = validate_integration_changes(old, current)
    manifest_rows = manifest.get("files", [])
    indexed = {r["path"]: r for r in manifest_rows}
    require(len(indexed) == len(manifest_rows), "duplicate public manifest path")
    runtime_paths = [p.relative_to(release).as_posix() for p in current_paths + old_paths]
    for relative in runtime_paths:
        raw = regular(release / relative)
        row = indexed.get(relative, {})
        is_historical = relative.startswith(HISTORICAL_PREFIX + "/")
        expected_commit = HISTORICAL_CORE if is_historical else commit
        source_name = "zerorun/" + Path(relative).name
        require(row.get("sha256") == digest(raw) and type(row.get("bytes")) is int
                and row["bytes"] == len(raw) and row.get("origin") == "git:" + expected_commit + ":" + source_name,
                "runtime manifest origin or bytes differ")
    test_paths = [p.relative_to(release).as_posix() for p in (release / "tests").rglob("*.py")]
    require(REQUIRED_TESTS <= set(test_paths), "required current MCP tests missing")
    for relative, expected in REQUIRED_SUPPORT.items():
        raw = regular(release / relative)
        require({"bytes": len(raw), "sha256": digest(raw)} == expected,
                "required integration test or bundled skill bytes differ: " + relative)
        row = indexed.get(relative, {})
        require(row.get("origin") == SUPPORT_ORIGINS[relative]
                and row.get("sha256") == digest(raw) and type(row.get("bytes")) is int
                and row["bytes"] == len(raw), "required integration support origin differs")
    require(_literal_assignment(current["codex.py"], "_SKILL")
            == regular(release / "docs/zerorun-SKILL.md").decode("utf-8").replace("\r\n", "\n"),
            "bundled skill and integration payload differ")
    tool_paths = [p.relative_to(release).as_posix() for p in (release / "tools").rglob("*.py")]
    return {"current_core_commit": commit, "historical_core_commit": HISTORICAL_CORE,
            "version": VERSION, "changed_runtime_modules": changed,
            "unchanged_computational_modules": 32, "model_change": "RunResult class docstring only",
            "integration_core_commit": CURRENT_CORE,
            "runtime_files": rows(release, runtime_paths),
            "test_and_helper_files": rows(release, test_paths + tool_paths + [SELF, historical.SELF, "pyproject.toml", "docs/zerorun-SKILL.md"])}

def wheel_check(release):
    release = Path(release)
    _bound_files({p.name: regular(p) for p in (release / "src/zerorun").glob("*.py")}, CURRENT_FILES, "wheel source runtime")
    raw = regular(release / WHEEL)
    with zipfile.ZipFile(release / WHEEL) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and archive.testzip() is None, "invalid wheel archive")
        expected = {"zerorun/" + p.name for p in (release / "src/zerorun").glob("*.py")}
        actual = {name for name in names if name.startswith("zerorun/") and name.endswith(".py")}
        require(expected == actual and len(actual) == 36, "wheel runtime inventory differs")
        dist_info = "zerorun-0.5.3.dist-info/"
        allowed_metadata = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt",
                            "licenses/LICENSE.txt", "licenses/Licence.txt", "licenses/THIRD_PARTY_NOTICES.md"}
        require(all(name in expected or (name.startswith(dist_info)
                    and name.removeprefix(dist_info) in allowed_metadata) for name in names),
                "unexpected executable or extra wheel payload")
        for name in actual:
            require(archive.read(name) == regular(release / "src" / name), "wheel runtime bytes differ")
        metadata = Parser().parsestr(archive.read("zerorun-0.5.3.dist-info/METADATA").decode())
        require(metadata.get("Version") == VERSION and metadata.get("Name") == "zerorun"
                and metadata.get("License-Expression") == "MIT" and not metadata.get_all("Requires-Dist"), "wheel metadata differs")
    return {"path": WHEEL, "bytes": len(raw), "sha256": digest(raw)}

def validate(release, receipt_path):
    release, receipt_path = Path(release), Path(receipt_path)
    record = strict_json(regular(receipt_path))
    require(record.get("schema") == SCHEMA and record.get("completed") is True
            and record.get("failure") is None, "current runtime attempt incomplete")
    actual = snapshot(release)
    require(canonical(record.get("source_before")) == canonical(record.get("source_after")) == canonical(actual),
            "current runtime/test source drift")
    require(canonical(record.get("wheel")) == canonical(wheel_check(release)), "tested wheel differs")
    calls = record.get("commands")
    require(isinstance(calls, list) and [r.get("label") for r in calls] == ["create_venv", "install_wheel", "installed_smoke", "installed_help", "public_regression"], "command sequence incomplete")
    for row in calls:
        require(type(row.get("returncode")) is int and row["returncode"] == 0 and row.get("error") is None, "current runtime command failed")
        decoded(row["stdout"]); decoded(row["stderr"])
    expected = {p.name: digest(regular(p)) for p in (release / "src/zerorun").glob("*.py")}
    installed = strict_json(decoded(calls[2]["stdout"]))
    require(installed.get("version") == VERSION and installed.get("files") == expected, "installed runtime differs")
    require("mcp-server" in decoded(calls[3]["stdout"]).decode()
            and "authorize" in decoded(calls[3]["stdout"]).decode(), "installed CLI incomplete")
    commands = [row["command"] for row in calls]
    require(record.get("platform") in {"win32", "linux", "darwin"}, "recorded host platform absent")
    path_type = PureWindowsPath if record["platform"] == "win32" else PurePosixPath
    venv = path_type(record["external_venv"])
    tested_release = path_type(record["tested_release_absolute"])
    output = path_type(record["output_directory_absolute"])
    require(all(path.is_absolute() and ".." not in path.parts for path in (venv, tested_release, output)),
            "recorded execution paths must be absolute and normalized")
    require(not venv.is_relative_to(tested_release), "installation is inside source checkout")
    require(path_type(installed["module"]).is_relative_to(venv), "smoke imported outside new venv")
    require(path_type(record["wheel_input_absolute"]) == tested_release / WHEEL,
            "installed wheel path differs from tested release")
    require(all(path_type(row["cwd"]) == venv.parent for row in calls[:4]),
            "installation or smoke working directory differs")
    installed_executable = str(venv / ("Scripts/python.exe" if record["platform"] == "win32" else "bin/python"))
    # Recorded paths may come from another OS; normalize separators for comparison.
    normalize = lambda value: value.replace("\\", "/")
    require(all(normalize(commands[i][0]) == normalize(installed_executable) for i in (1, 2, 3)),
            "installation/smoke did not use the same new environment")
    require(len(commands[0]) == 4 and commands[0][1:] == ["-m", "venv", record["external_venv"]]
            and len(commands[1]) == 10 and commands[1][1:] == ["-I", "-B", "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", str(record["wheel_input_absolute"])]
            and len(commands[2]) == 5 and commands[2][1:] == ["-I", "-B", "-c", SMOKE]
            and len(commands[3]) == 6 and commands[3][1:] == ["-I", "-B", "-m", "zerorun", "--help"], "install/smoke command differs")
    require(len(commands[4]) == 12
            and commands[4][1:5] == ["-B", "-c", TEST_SCRIPT, str(record["tested_release_absolute"])]
            and commands[4][5:10] == ["tests", "-q", "--import-mode=importlib", "-p", "no:cacheprovider"]
            and normalize(commands[4][10]) == "--basetemp=" + normalize(str(venv.parent / "cases"))
            and normalize(commands[4][11]) == "--junitxml=" + normalize(record["output_directory_absolute"]) + "/tests.xml",
            "published regression command differs")
    require(record["tested_release_absolute"] == calls[4]["cwd"], "regression working directory differs")
    xml_path = receipt_path.parent / "tests.xml"
    raw = regular(xml_path)
    require(record.get("junit") == {"path": "tests.xml", "bytes": len(raw), "sha256": digest(raw)}, "JUnit bytes differ")
    counts = junit_counts(raw, pytest_stdout=decoded(calls[4]["stdout"]))
    require(canonical(record.get("junit_counts")) == canonical(counts) and record.get("passed") is True,
            "JUnit/receipt result differs")
    require(record.get("model_called") is False and record.get("docker_called") is False
            and record.get("authority_changed") is False and record.get("historical_evidence_reclassified") is False, "current runtime scope differs")
    return {"schema": SCHEMA, "passed": True, "version": VERSION,
            "current_core_commit": actual["current_core_commit"], "historical_core_commit": HISTORICAL_CORE,
            "changed_runtime_modules": actual["changed_runtime_modules"], "installed_runtime_files": 36,
            "unchanged_computational_modules": 32, "model_change": "RunResult class docstring only",
            "junit_counts": counts, "receipt_sha256": digest(regular(receipt_path)), "wheel": record["wheel"]}

def run(release, output):
    release, output = Path(release).resolve(), Path(output).absolute()
    require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(release), "new external output directory required")
    output.mkdir()
    external = Path(tempfile.mkdtemp(prefix="zerorun-current-runtime-053-", dir=output.parent))
    require(not any((p / ".git").exists() for p in (external, *external.parents)), "temporary environment has Git ancestor")
    venv = external / "venv"
    record = {"schema": SCHEMA, "started_utc": datetime.now(timezone.utc).isoformat(),
              "completed": False, "failure": None, "commands": [], "passed": False,
              "external_venv": str(venv), "tested_release_absolute": str(release),
              "output_directory_absolute": str(output), "platform": sys.platform,
              "wheel_input_absolute": str(release / WHEEL), "model_called": False,
              "docker_called": False, "authority_changed": False, "historical_evidence_reclassified": False}
    try:
        require(regular(Path(__file__)) == regular(release / SELF), "executed producer differs from release")
        require(regular(Path(historical.__file__)) == regular(release / historical.SELF), "shared historical helper differs from release")
        record["source_before"] = snapshot(release)
        record["wheel"] = wheel_check(release)
        executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        commands = [
            ("create_venv", [sys.executable, "-m", "venv", str(venv)], external),
            ("install_wheel", [str(executable), "-I", "-B", "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", str(release / WHEEL)], external),
            ("installed_smoke", [str(executable), "-I", "-B", "-c", SMOKE], external),
            ("installed_help", [str(executable), "-I", "-B", "-m", "zerorun", "--help"], external),
            ("public_regression", [sys.executable, "-B", "-c", TEST_SCRIPT, str(release), "tests", "-q", "--import-mode=importlib", "-p", "no:cacheprovider", "--basetemp=" + str(external / "cases"), "--junitxml=" + str(output / "tests.xml")], release),
        ]
        environment = execution_environment()
        for label, command, cwd in commands:
            row = invoke(label, command, cwd=cwd, env=environment)
            record["commands"].append(row)
            require(row["returncode"] == 0 and row["error"] is None, "command failed: " + label)
        xml = regular(output / "tests.xml")
        record["junit"] = {"path": "tests.xml", "bytes": len(xml), "sha256": digest(xml)}
        record["junit_counts"] = junit_counts(xml, pytest_stdout=decoded(record["commands"][4]["stdout"]))
        record["source_after"] = snapshot(release)
        record["completed"] = True
        record["passed"] = record["source_before"] == record["source_after"]
    except Exception as exc:
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()
    path = output / "receipt.json"
    path.write_bytes(json.dumps(record, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n")
    if record["passed"]:
        result = validate(release, path)
        print(json.dumps(result, sort_keys=True))
        return 0
    print(json.dumps({"passed": False, "failure": record["failure"], "receipt": str(path)}))
    return 1

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(validate(args.release, args.check), sort_keys=True))
        return 0
    return run(args.release, args.output)

if __name__ == "__main__":
    raise SystemExit(main())
