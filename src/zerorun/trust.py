from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bounded_json import JsonLimits, loads_bounded_json
from .manifest import read_regular_manifest_bytes
from .model import ConfigurationError, Manifest
from .oci import _TRUNCATION_MARKER, _run_bounded_process
from .path_safety import is_link_like as _path_is_link_like


_AUTHORITY_SCHEMA = "zerorun-user-authority-v1"
_PROVENANCE_SCHEMA = "zerorun-cache-provenance-v1"
_SECRET_BYTES = 32
_HEX_64 = frozenset("0123456789abcdef")
_MAX_GIT_MARKER_BYTES = 4 * 1024
_MAX_AUTHORITY_RECEIPT_BYTES = 64 * 1024
_MAX_GIT_TRUST_OUTPUT_BYTES = 64 * 1024
_AUTHORITY_JSON_LIMITS = JsonLimits(
    max_bytes=_MAX_AUTHORITY_RECEIPT_BYTES,
    max_depth=3,
    max_values=32,
    max_object_members=16,
    max_structural_tokens=64,
    max_number_chars=64,
    max_string_chars=1_024,
    max_total_string_chars=8_192,
)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("trust payload is not finite canonical JSON") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX_64 for character in value)
    )


def _is_link_like(path: Path) -> bool:
    try:
        return _path_is_link_like(path)
    except OSError as exc:
        raise ConfigurationError(f"cannot safely inspect trust path {path}: {exc}") from exc


def repository_marker_kind(root: Path) -> str | None:
    """Classify a repository marker without following attacker-controlled links.

    A directory is the normal checkout form and a regular file is Git's linked
    worktree form.  Anything else is not a repository boundary.  In particular,
    a dangling link must not be skipped while searching for a parent checkout.
    """

    marker = Path(os.path.abspath(root.expanduser())) / ".git"
    if _is_link_like(marker):
        raise ConfigurationError(
            f"repository .git marker must be a regular file or directory, not a link: {marker}"
        )
    try:
        if marker.is_dir():
            return "directory"
        if marker.is_file():
            return "file"
        if marker.exists():
            raise ConfigurationError(
                f"repository .git marker is not a regular file or directory: {marker}"
            )
    except OSError as exc:
        raise ConfigurationError(
            f"cannot safely inspect repository .git marker {marker}: {exc}"
        ) from exc
    return None


def _default_trust_root() -> Path:
    """Return per-user authority storage, never repository-local state.

    ``ZERORUN_TRUST_ROOT`` is an explicit deployment/test override. MCP task
    environments cannot forward repository-selected variables, so checkout
    content cannot set it through a ZeroRun manifest.
    """

    override = os.environ.get("ZERORUN_TRUST_ROOT")
    if override:
        candidate = Path(override).expanduser()
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        candidate = (
            Path(base) / "ZeroRun" / "trust-v1"
            if base
            else Path.home() / "AppData" / "Local" / "ZeroRun" / "trust-v1"
        )
    else:
        state_home = os.environ.get("XDG_STATE_HOME")
        candidate = (
            Path(state_home) / "zerorun" / "trust-v1"
            if state_home
            else Path.home() / ".local" / "state" / "zerorun" / "trust-v1"
        )
    if not candidate.is_absolute():
        raise ConfigurationError("ZeroRun trust root must be an absolute path")
    return Path(os.path.abspath(candidate))


def _assert_external_trust_root(root: Path, trust_root: Path) -> Path:
    root = root.expanduser().resolve(strict=True)
    trust_root = Path(os.path.abspath(trust_root.expanduser()))
    try:
        trust_root.relative_to(root)
    except ValueError:
        pass
    else:
        raise ConfigurationError(
            "ZeroRun user authority must be stored outside the repository"
        )
    try:
        root.relative_to(trust_root)
    except ValueError:
        pass
    else:
        raise ConfigurationError(
            "ZeroRun repository must not be located inside its user authority directory"
        )

    # Inspect every lexical component that already exists.  Resolving first
    # would hide the very symlink/junction redirection this boundary prevents.
    anchor = Path(trust_root.anchor)
    cursor = anchor
    for part in trust_root.parts[1:] if trust_root.anchor else trust_root.parts:
        cursor = cursor / part
        if _is_link_like(cursor):
            raise ConfigurationError(
                f"ZeroRun user authority path contains a symbolic link or junction: {cursor}"
            )
        if cursor.exists() and not cursor.is_dir():
            raise ConfigurationError(
                f"ZeroRun user authority path contains a non-directory component: {cursor}"
            )
    return trust_root


def _assert_directory_chain_unlinked(path: Path) -> None:
    anchor = Path(path.anchor)
    cursor = anchor
    for part in path.parts[1:] if path.anchor else path.parts:
        cursor = cursor / part
        if _is_link_like(cursor):
            raise ConfigurationError(
                f"ZeroRun user authority path contains a symbolic link or junction: {cursor}"
            )
        if cursor.exists() and not cursor.is_dir():
            raise ConfigurationError(
                f"ZeroRun user authority path contains a non-directory component: {cursor}"
            )


def _ensure_private_directory(path: Path) -> None:
    _assert_directory_chain_unlinked(path)
    path.mkdir(parents=True, exist_ok=True)
    _assert_directory_chain_unlinked(path)
    if _is_link_like(path) or not path.is_dir():
        raise ConfigurationError(
            f"ZeroRun user authority directory is not a private regular directory: {path}"
        )
    if os.name != "nt":
        os.chmod(path, 0o700)
        details = path.stat()
        if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) & 0o077:
            raise ConfigurationError(
                f"ZeroRun user authority directory is not private to the current user: {path}"
            )


def _open_regular_readonly(path: Path, *, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError(f"could not read {label}: {exc}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ConfigurationError(f"{label} is not a regular file: {path}")
        if os.name != "nt":
            if details.st_uid != os.geteuid():
                raise ConfigurationError(f"{label} is not owned by the current user")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_regular_bytes(
    path: Path, *, label: str, max_bytes: int | None = None
) -> bytes:
    if _is_link_like(path):
        raise ConfigurationError(f"{label} is not a regular file: {path}")
    descriptor = _open_regular_readonly(path, label=label)
    try:
        opened = os.fstat(descriptor)
        if max_bytes is not None and opened.st_size > max_bytes:
            raise ConfigurationError(f"{label} exceeds the {max_bytes}-byte safety limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1 if max_bytes is not None else None
        while remaining is None or remaining > 0:
            chunk = os.read(
                descriptor,
                64 * 1024 if remaining is None else min(64 * 1024, remaining),
            )
            if not chunk:
                break
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        payload = b"".join(chunks)
        if max_bytes is not None and len(payload) > max_bytes:
            raise ConfigurationError(f"{label} exceeds the {max_bytes}-byte safety limit")
        after_open = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ConfigurationError(f"{label} changed while it was being read") from exc
        if (
            len(payload) != opened.st_size
            or _is_link_like(path)
            or not stat.S_ISREG(current.st_mode)
            or (
                after_open.st_dev,
                after_open.st_ino,
                after_open.st_mode,
                after_open.st_size,
                after_open.st_mtime_ns,
                getattr(after_open, "st_ctime_ns", None),
            )
            != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                getattr(opened, "st_ctime_ns", None),
            )
            or (
                current.st_dev,
                current.st_ino,
                current.st_mode,
                current.st_size,
                current.st_mtime_ns,
            )
            != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
            )
        ):
            raise ConfigurationError(f"{label} changed while it was being read")
        return payload
    finally:
        os.close(descriptor)


def _secret_path(root: Path) -> Path:
    return _assert_external_trust_root(root, _default_trust_root()) / "authority.key"


def _read_secret(root: Path, *, create: bool) -> bytes | None:
    path = _secret_path(root)
    if path.exists() or _is_link_like(path):
        value = _read_regular_bytes(
            path,
            label="ZeroRun user authority key",
            max_bytes=_SECRET_BYTES,
        )
        if len(value) != _SECRET_BYTES:
            raise ConfigurationError("ZeroRun user authority key has an invalid length")
        if os.name != "nt":
            details = path.stat(follow_symlinks=False)
            if stat.S_IMODE(details.st_mode) & 0o077:
                raise ConfigurationError("ZeroRun user authority key permissions are too broad")
        return value
    if not create:
        return None

    _ensure_private_directory(path.parent)
    value = secrets.token_bytes(_SECRET_BYTES)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".authority-key-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.write(descriptor, value)
        os.fsync(descriptor)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    try:
        # Hard-link publication gives O_EXCL semantics while making the complete
        # key visible atomically to concurrent ZeroRun processes.
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = _read_secret(root, create=False)
            if existing is None:
                raise ConfigurationError("ZeroRun authority key publication raced")
            return existing
        except OSError as exc:
            raise ConfigurationError(
                f"could not atomically publish ZeroRun user authority key: {exc}"
            ) from exc
        return value
    finally:
        temporary.unlink(missing_ok=True)


def repository_identity(root: Path) -> str:
    """Bind authority to one canonical checkout, not merely a path string."""

    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ConfigurationError(f"ZeroRun repository root is not a directory: {root}")
    root_stat = root.stat()
    git = root / ".git"
    marker_kind = repository_marker_kind(root)
    git_row: dict[str, Any]
    if marker_kind == "directory":
        git_stat = git.stat()
        git_row = {
            "kind": "directory",
            "device": int(git_stat.st_dev),
            "inode": int(git_stat.st_ino),
        }
    elif marker_kind == "file":
        marker = _read_regular_bytes(
            git,
            label="repository .git marker",
            max_bytes=_MAX_GIT_MARKER_BYTES,
        )
        git_row = {"kind": "file", "sha256": _sha256_bytes(marker)}
    else:
        git_row = {"kind": "absent"}
    identity = {
        "schema": 1,
        "canonical_root": os.path.normcase(str(root)),
        "root_device": int(root_stat.st_dev),
        "root_inode": int(root_stat.st_ino),
        "git": git_row,
    }
    return _sha256_bytes(_canonical_bytes(identity))


def manifest_sha256(manifest: Manifest) -> str:
    path = manifest.path
    try:
        path.relative_to(manifest.root)
    except ValueError as exc:
        raise ConfigurationError("ZeroRun manifest path escapes its repository") from exc
    digest = manifest.source_sha256
    if not _is_lower_sha256(digest):
        raise ConfigurationError(
            "ZeroRun manifest was not loaded from stable repository bytes"
        )
    _, current = read_regular_manifest_bytes(path)
    if _sha256_bytes(current) != digest:
        raise ConfigurationError("ZeroRun manifest changed after it was loaded")
    return digest


def manifest_sha256_for_root(root: Path) -> str:
    path = root.expanduser().resolve(strict=True) / ".zerorun.json"
    try:
        _, payload = read_regular_manifest_bytes(path)
    except ConfigurationError as exc:
        raise ConfigurationError(
            "cache provenance requires the repository's regular .zerorun.json"
        ) from exc
    return _sha256_bytes(payload)


def _git_environment() -> dict[str, str]:
    keep = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "HOME",
    )
    environment = {name: os.environ[name] for name in keep if name in os.environ}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def tracked_zerorun_paths(root: Path) -> tuple[str, ...]:
    """Return tracked runtime-state paths; malformed/non-Git fixtures are not Git."""

    root = root.expanduser().resolve(strict=True)
    marker = root / ".git"
    marker_kind = repository_marker_kind(root)
    if marker_kind is None:
        return ()
    discovered = shutil.which("git")
    if discovered is None:
        # A real Git checkout without a trustworthy Git client cannot prove the
        # index clean, so fail closed. Empty .git test fixtures are not real.
        if (marker / "HEAD").exists() if marker_kind == "directory" else True:
            raise ConfigurationError(
                "cannot verify that .zerorun runtime state is untracked: Git is unavailable"
            )
        return ()
    git = Path(discovered).expanduser().resolve(strict=True)
    try:
        git.relative_to(root)
    except ValueError:
        pass
    else:
        raise ConfigurationError(
            "refusing repository-controlled Git executable for trust verification"
        )
    common = [
        str(git),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-C",
        str(root),
    ]
    try:
        probe, probe_timed_out = _run_bounded_process(
            [*common, "rev-parse", "--show-toplevel"],
            cwd=None,
            environment=_git_environment(),
            timeout_seconds=10,
            output_limit_bytes=_MAX_GIT_TRUST_OUTPUT_BYTES,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"could not verify repository trust boundary with Git: {exc}"
        ) from exc
    if probe_timed_out:
        raise ConfigurationError(
            "could not verify repository trust boundary with Git: operation timed out"
        )
    if probe.returncode != 0:
        if marker_kind == "directory" and not (marker / "HEAD").exists():
            return ()
        raise ConfigurationError(
            "cannot verify that .zerorun runtime state is untracked"
        )
    if probe.stdout.startswith(_TRUNCATION_MARKER):
        raise ConfigurationError("Git repository root response exceeded its safety limit")
    try:
        top = Path(probe.stdout.decode("utf-8").strip()).resolve(strict=True)
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError("Git returned an invalid repository root") from exc
    if top != root:
        raise ConfigurationError(
            f"ZeroRun trust root must be the canonical Git top level: {top}"
        )
    try:
        listed, listed_timed_out = _run_bounded_process(
            [*common, "ls-files", "-z", "--", ".zerorun"],
            cwd=None,
            environment=_git_environment(),
            timeout_seconds=10,
            output_limit_bytes=_MAX_GIT_TRUST_OUTPUT_BYTES,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"could not inspect tracked ZeroRun runtime state: {exc}"
        ) from exc
    if listed_timed_out:
        raise ConfigurationError("inspection of tracked ZeroRun runtime state timed out")
    if listed.returncode != 0:
        raise ConfigurationError("could not inspect tracked ZeroRun runtime state")
    if listed.stdout.startswith(_TRUNCATION_MARKER):
        raise ConfigurationError(
            "tracked ZeroRun runtime state exceeds the bounded Git output limit"
        )
    try:
        return tuple(
            sorted(
                path
                for path in listed.stdout.decode("utf-8").split("\0")
                if path
            )
        )
    except UnicodeDecodeError as exc:
        raise ConfigurationError("Git returned non-UTF-8 tracked paths") from exc


def reject_tracked_zerorun_state(root: Path) -> None:
    tracked = tracked_zerorun_paths(root)
    if tracked:
        sample = ", ".join(tracked[:4])
        raise ConfigurationError(
            "Git-tracked .zerorun runtime state is untrusted and cannot be used"
            + (f": {sample}" if sample else "")
        )


def _mac(secret: bytes, payload: dict[str, Any]) -> str:
    return hmac.new(secret, _canonical_bytes(payload), hashlib.sha256).hexdigest()


def _validate_cache_binding_digests(
    *,
    manifest_digest: str,
    profile_digest: str | None,
) -> None:
    if not _is_lower_sha256(manifest_digest):
        raise ConfigurationError("cache provenance manifest digest is malformed")
    if profile_digest is not None and not _is_lower_sha256(profile_digest):
        raise ConfigurationError("cache provenance profile digest is malformed")


def _cache_binding(
    metadata: dict[str, Any],
    *,
    repository_digest: str,
    manifest_digest: str,
    profile_digest: str | None,
) -> dict[str, Any]:
    _validate_cache_binding_digests(
        manifest_digest=manifest_digest,
        profile_digest=profile_digest,
    )
    if not _is_lower_sha256(repository_digest):
        raise ConfigurationError("cache provenance repository identity is malformed")
    unsigned = dict(metadata)
    unsigned.pop("provenance", None)
    return {
        "schema": _PROVENANCE_SCHEMA,
        "repository_identity": repository_digest,
        "manifest_sha256": manifest_digest,
        "profile_sha256": profile_digest,
        "result_sha256": _sha256_bytes(_canonical_bytes(unsigned)),
    }


@dataclass
class CacheTrustSession:
    """Bound, phase-local cache authority with exact pre/post attestation.

    Cache payloads remain independently fingerprinted and HMAC-verified. The
    session only amortizes repository-wide checks that are identical for every
    entry in one lookup or publication phase. A session is single-use and must
    be verified after the phase before provisional hits may escape.
    """

    root: Path
    manifest_digest: str
    profile_digest: str | None
    repository_digest: str
    trust_root: Path
    tracked_paths: tuple[str, ...]
    _secret: bytes | None = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @classmethod
    def open(
        cls,
        root: Path,
        *,
        manifest_digest: str,
        profile_digest: str | None = None,
        create_secret: bool = False,
    ) -> "CacheTrustSession":
        canonical_root = root.expanduser().resolve(strict=True)
        _validate_cache_binding_digests(
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
        )
        actual_manifest = manifest_sha256_for_root(canonical_root)
        if actual_manifest != manifest_digest:
            raise ConfigurationError(
                "cache trust session manifest does not match the repository"
            )
        tracked = tracked_zerorun_paths(canonical_root)
        if tracked:
            sample = ", ".join(tracked[:4])
            raise ConfigurationError(
                "Git-tracked .zerorun runtime state is untrusted and cannot be used"
                + (f": {sample}" if sample else "")
            )
        trust_root = _assert_external_trust_root(
            canonical_root,
            _default_trust_root(),
        )
        return cls(
            root=canonical_root,
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
            repository_digest=repository_identity(canonical_root),
            trust_root=trust_root,
            tracked_paths=tracked,
            _secret=_read_secret(canonical_root, create=create_secret),
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_active_binding(
        self,
        *,
        manifest_digest: str,
        profile_digest: str | None,
    ) -> None:
        if self._closed:
            raise ConfigurationError("cache trust session is already closed")
        _validate_cache_binding_digests(
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
        )
        if (
            manifest_digest != self.manifest_digest
            or profile_digest != self.profile_digest
        ):
            raise ConfigurationError(
                "cache trust session was used with a different provenance binding"
            )

    def payload_is_trusted(
        self,
        metadata: dict[str, Any],
        *,
        manifest_digest: str,
        profile_digest: str | None = None,
    ) -> bool:
        self._require_active_binding(
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
        )
        try:
            unsigned = dict(metadata)
            provenance = unsigned.pop("provenance", None)
            if not isinstance(provenance, dict):
                return False
            binding = _cache_binding(
                unsigned,
                repository_digest=self.repository_digest,
                manifest_digest=manifest_digest,
                profile_digest=profile_digest,
            )
            supplied_mac = provenance.get("mac")
            return (
                self._secret is not None
                and set(provenance) == {*binding, "mac"}
                and isinstance(supplied_mac, str)
                and _is_lower_sha256(supplied_mac)
                and all(
                    provenance.get(name) == value
                    for name, value in binding.items()
                )
                and hmac.compare_digest(
                    supplied_mac,
                    _mac(self._secret, binding),
                )
            )
        except ConfigurationError:
            return False

    def sign_payload(
        self,
        metadata: dict[str, Any],
        *,
        manifest_digest: str,
        profile_digest: str | None = None,
    ) -> dict[str, Any]:
        self._require_active_binding(
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
        )
        if self._secret is None:
            raise ConfigurationError(
                "cache publication trust session has no user authority key"
            )
        binding = _cache_binding(
            metadata,
            repository_digest=self.repository_digest,
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
        )
        return {**binding, "mac": _mac(self._secret, binding)}

    def verify_unchanged(self) -> None:
        """Close this phase and reject any trust-boundary drift."""

        if self._closed:
            raise ConfigurationError("cache trust session is already closed")
        try:
            current_trust_root = _assert_external_trust_root(
                self.root,
                _default_trust_root(),
            )
            if current_trust_root != self.trust_root:
                raise ConfigurationError(
                    "cache trust root changed during the request"
                )
            current_tracked = tracked_zerorun_paths(self.root)
            if current_tracked != self.tracked_paths or current_tracked:
                raise ConfigurationError(
                    "Git-tracked .zerorun state changed during the request"
                )
            if manifest_sha256_for_root(self.root) != self.manifest_digest:
                raise ConfigurationError(
                    "cache trust manifest changed during the request"
                )
            if repository_identity(self.root) != self.repository_digest:
                raise ConfigurationError(
                    "cache trust repository identity changed during the request"
                )
            current_secret = _read_secret(self.root, create=False)
            if current_secret != self._secret:
                raise ConfigurationError(
                    "cache trust authority key changed during the request"
                )
        finally:
            self._closed = True

    def abort(self) -> None:
        """Make a failed phase's session unusable without accepting it."""

        self._closed = True


def _authority_payload(
    manifest: Manifest,
    *,
    profile_sha256: str | None,
) -> dict[str, Any]:
    if profile_sha256 is not None and not _is_lower_sha256(profile_sha256):
        raise ConfigurationError("pytest profile authority digest is malformed")
    return {
        "schema": _AUTHORITY_SCHEMA,
        "repository_identity": repository_identity(manifest.root),
        "manifest_sha256": manifest_sha256(manifest),
        "profile_sha256": profile_sha256,
    }


def _authority_path(manifest: Manifest, payload: dict[str, Any]) -> Path:
    trust_root = _assert_external_trust_root(manifest.root, _default_trust_root())
    identity = str(payload["repository_identity"])
    digest = _sha256_bytes(_canonical_bytes(payload))
    return trust_root / "repositories" / identity / "authorities" / f"{digest}.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    if path.exists() or _is_link_like(path):
        if _is_link_like(path) or not path.is_file():
            raise ConfigurationError(f"ZeroRun authority receipt is not a regular file: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".authority-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def authorize_manifest(
    manifest: Manifest,
    *,
    expected_manifest_sha256: str,
) -> str:
    """Persist user authority only when the caller names the exact digest."""

    if repository_marker_kind(manifest.root) is None:
        raise ConfigurationError(
            "external reuse authority requires a repository with a regular .git marker"
        )
    reject_tracked_zerorun_state(manifest.root)
    actual = manifest_sha256(manifest)
    if expected_manifest_sha256 != actual:
        raise ConfigurationError(
            f"manifest authorization digest mismatch; exact digest is {actual}"
        )
    payload = _authority_payload(manifest, profile_sha256=None)
    secret = _read_secret(manifest.root, create=True)
    assert secret is not None
    receipt = {**payload, "authorized_at": time.time()}
    receipt["mac"] = _mac(secret, payload)
    _write_json_atomic(_authority_path(manifest, payload), receipt)
    return actual


def authorize_pytest_profile(manifest: Manifest, profile_sha256: str) -> None:
    if repository_marker_kind(manifest.root) is None:
        raise ConfigurationError(
            "external reuse authority requires a repository with a regular .git marker"
        )
    reject_tracked_zerorun_state(manifest.root)
    payload = _authority_payload(manifest, profile_sha256=profile_sha256)
    secret = _read_secret(manifest.root, create=True)
    assert secret is not None
    receipt = {**payload, "authorized_at": time.time()}
    receipt["mac"] = _mac(secret, payload)
    _write_json_atomic(_authority_path(manifest, payload), receipt)
    # A reviewed profile also authorizes the exact carrier manifest.
    authorize_manifest(
        manifest,
        expected_manifest_sha256=str(payload["manifest_sha256"]),
    )


def _is_authorized(manifest: Manifest, *, profile_sha256: str | None) -> bool:
    try:
        reject_tracked_zerorun_state(manifest.root)
        payload = _authority_payload(manifest, profile_sha256=profile_sha256)
        path = _authority_path(manifest, payload)
        if _is_link_like(path) or not path.is_file():
            return False
        raw = loads_bounded_json(
            _read_regular_bytes(
                path,
                label="ZeroRun authority receipt",
                max_bytes=_MAX_AUTHORITY_RECEIPT_BYTES,
            ),
            label="ZeroRun authority receipt",
            limits=_AUTHORITY_JSON_LIMITS,
        )
        if not isinstance(raw, dict):
            return False
        secret = _read_secret(manifest.root, create=False)
        if secret is None:
            return False
        supplied_mac = raw.get("mac")
        authorized_at = raw.get("authorized_at")
        return (
            set(raw) == {*payload, "authorized_at", "mac"}
            and isinstance(supplied_mac, str)
            and _is_lower_sha256(supplied_mac)
            and isinstance(authorized_at, (int, float))
            and not isinstance(authorized_at, bool)
            and all(raw.get(field) == value for field, value in payload.items())
            and hmac.compare_digest(supplied_mac, _mac(secret, payload))
        )
    except (ConfigurationError, OSError):
        return False


def manifest_is_authorized(manifest: Manifest) -> bool:
    return _is_authorized(manifest, profile_sha256=None)


def pytest_profile_is_authorized(manifest: Manifest, profile_sha256: str) -> bool:
    return _is_authorized(manifest, profile_sha256=profile_sha256)


def require_manifest_authority(manifest: Manifest) -> None:
    if not manifest_is_authorized(manifest):
        digest = manifest_sha256(manifest)
        raise ConfigurationError(
            "Codex/MCP reuse refuses an untrusted repository manifest; authorize "
            f"the exact SHA-256 outside the checkout first: {digest}"
        )


def require_pytest_profile_authority(manifest: Manifest, profile_sha256: str) -> None:
    if not pytest_profile_is_authorized(manifest, profile_sha256):
        raise ConfigurationError(
            "pytest reuse refuses a profile without matching external user authority"
        )


def sign_cache_payload(
    root: Path,
    metadata: dict[str, Any],
    *,
    manifest_digest: str,
    profile_digest: str | None = None,
) -> dict[str, Any]:
    reject_tracked_zerorun_state(root)
    binding = _cache_binding(
        metadata,
        repository_digest=repository_identity(root),
        manifest_digest=manifest_digest,
        profile_digest=profile_digest,
    )
    secret = _read_secret(root, create=True)
    assert secret is not None
    return {**binding, "mac": _mac(secret, binding)}


def cache_payload_is_trusted(
    root: Path,
    metadata: dict[str, Any],
    *,
    manifest_digest: str,
    profile_digest: str | None = None,
) -> bool:
    try:
        reject_tracked_zerorun_state(root)
        unsigned = dict(metadata)
        provenance = unsigned.pop("provenance", None)
        if not isinstance(provenance, dict):
            return False
        binding = _cache_binding(
            unsigned,
            repository_digest=repository_identity(root),
            manifest_digest=manifest_digest,
            profile_digest=profile_digest,
        )
        secret = _read_secret(root, create=False)
        supplied_mac = provenance.get("mac")
        return (
            secret is not None
            and set(provenance) == {*binding, "mac"}
            and isinstance(supplied_mac, str)
            and _is_lower_sha256(supplied_mac)
            and all(provenance.get(field) == value for field, value in binding.items())
            and hmac.compare_digest(supplied_mac, _mac(secret, binding))
        )
    except ConfigurationError:
        return False
