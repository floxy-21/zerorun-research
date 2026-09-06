from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator, cast

import pytest


_ORIGINAL_OS_MKDIR = os.mkdir
_ORIGINAL_TEMPFILE_OS = tempfile._os


@contextmanager
def _pytest_temp_path_creation() -> Iterator[None]:
    """Keep pytest's private temp dirs usable by restricted Windows tokens.

    CPython 3.13+ gives ``mkdir(..., 0o700)`` a special restrictive ACL on
    Windows.  A restricted Codex token can create that ACL and then be unable
    to traverse it.  Pytest uses that exact mode for its base and per-test
    directories.  Translate only those synchronous factory calls to the normal
    inherited Windows ACL, then restore ``os.mkdir`` before fixture setup
    continues or product code runs.
    """

    if os.name != "nt":
        yield
        return

    original = os.mkdir

    def compatible_mkdir(
        path: os.PathLike[str] | str | bytes,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        usable_mode = 0o777 if mode == 0o700 else mode
        if dir_fd is None:
            original(path, usable_mode)
        else:
            original(path, usable_mode, dir_fd=dir_fd)

    os.mkdir = compatible_mkdir
    try:
        yield
    finally:
        os.mkdir = original


class _WindowsCompatibleTempfileOS:
    """Delegate tempfile operations while avoiding its restrictive 0700 DACL."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def mkdir(
        self,
        path: os.PathLike[str] | str | bytes,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        if dir_fd is None:
            if mode == 0o700:
                self._backend.mkdir(path)
            else:
                self._backend.mkdir(path, mode)
        else:
            self._backend.mkdir(path, mode, dir_fd=dir_fd)


@pytest.fixture(scope="session", autouse=True)
def _windows_compatible_stdlib_temp_directories() -> Iterator[None]:
    """Keep stdlib temp directories usable without patching product ``os``."""

    if os.name != "nt":
        yield
        return

    tempfile._os = _WindowsCompatibleTempfileOS(_ORIGINAL_TEMPFILE_OS)
    try:
        yield
    finally:
        tempfile._os = _ORIGINAL_TEMPFILE_OS


class _WindowsCompatibleTempPathFactory:
    """Delegate to pytest while narrowly adapting its Windows mkdir mode."""

    def __init__(self, factory: pytest.TempPathFactory) -> None:
        self._factory = factory

    def getbasetemp(self) -> Path:
        with _pytest_temp_path_creation():
            return self._factory.getbasetemp()

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        with _pytest_temp_path_creation():
            return self._factory.mktemp(basename, numbered=numbered)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._factory, name)


@pytest.fixture(scope="session")
def tmp_path_factory(request: pytest.FixtureRequest) -> pytest.TempPathFactory:
    """Wrap pytest's own factory without changing its naming or retention."""

    factory = cast(pytest.TempPathFactory, request.config._tmp_path_factory)
    if os.name != "nt":
        return factory
    return cast(
        pytest.TempPathFactory,
        _WindowsCompatibleTempPathFactory(factory),
    )


@pytest.fixture
def original_os_mkdir_before_temp_factory():
    """Expose the pre-harness function for the restoration regression guard."""

    return _ORIGINAL_OS_MKDIR


@pytest.fixture(autouse=True)
def _isolated_zerorun_user_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Keep per-user trust material produced by tests out of the real profile."""

    trust_root = Path(tmp_path_factory.getbasetemp()) / "_zerorun-user-authority"
    monkeypatch.setenv("ZERORUN_TRUST_ROOT", str(trust_root.resolve()))
