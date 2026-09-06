from __future__ import annotations

import io
import subprocess
from pathlib import Path

from zerorun import runner
from zerorun.model import TaskSpec


def _task() -> TaskSpec:
    return TaskSpec(
        name="legacy",
        command=("python", "-c", "print('ok')"),
        inputs=(),
        outputs=(),
        env=(),
        cacheable=True,
        unsafe_effects=(),
        cache_streams=True,
    )


def test_legacy_host_success_is_not_cacheable_after_output_truncation(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run(command, **kwargs):
        assert kwargs["timeout_seconds"] == runner.HOST_EXECUTION_TIMEOUT_SECONDS
        assert kwargs["output_limit_bytes"] == runner.HOST_OUTPUT_LIMIT_BYTES
        return (
            subprocess.CompletedProcess(
                command,
                0,
                runner._TRUNCATION_MARKER + b"tail",
                b"",
            ),
            False,
        )

    monkeypatch.setattr(runner, "_run_bounded_process", fake_run)

    code, _elapsed, stdout, stderr = runner._execute(
        _task(), tmp_path, {}, ["python", "-c", "print('ok')"]
    )

    assert code == 74
    assert stdout.startswith(runner._TRUNCATION_MARKER)
    assert b"result was not cached" in stderr


def test_observe_bounds_output_and_timeout(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, **kwargs):
        return (
            subprocess.CompletedProcess(command, 0, b"", b"partial"),
            True,
        )

    monkeypatch.setattr(runner, "_run_bounded_process", fake_run)
    stdout = io.BytesIO()
    stderr = io.BytesIO()

    result = runner.observe(
        ["python", "-m", "pytest"],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert result.exit_code == 124
    assert result.status == "OBSERVED_EXECUTED"
    assert stdout.getvalue() == b""
    assert b"exceeded the 900 second limit" in stderr.getvalue()
