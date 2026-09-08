
import hashlib
import json
import os

_nodeids = []
_outcomes = {}


def pytest_collection_finish(session):
    global _nodeids
    _nodeids = [str(item.nodeid) for item in session.items]


def pytest_runtest_logreport(report):
    nodeid = str(report.nodeid)
    row = _outcomes.setdefault(
        nodeid,
        {"setup": None, "call": None, "teardown": None, "wasxfail": False},
    )
    when = str(report.when)
    if when in row:
        row[when] = str(report.outcome)
    row["wasxfail"] = bool(getattr(report, "wasxfail", False))


def pytest_sessionfinish(session, exitstatus):
    output = os.environ["ZERORUN_BENCHMARK_SHADOW_OUTPUT"]
    canonical = json.dumps(
        {"nodeids": _nodeids},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    payload = {
        "schema": "zerorun.benchmark-independent-pytest-shadow.v1",
        "exit_code": int(exitstatus),
        "nodeids": _nodeids,
        "nodeid_sha256": hashlib.sha256(canonical).hexdigest(),
        "outcomes": [_outcomes.get(nodeid) for nodeid in _nodeids],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    with open(output, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
