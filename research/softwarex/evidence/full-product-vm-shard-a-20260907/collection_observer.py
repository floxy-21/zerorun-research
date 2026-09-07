"""External observer: records pytest collection and reports; never selects tests."""
import json
from pathlib import Path
OUTPUT = Path('/home/floxy/zerorun-five-hour-20260907/full-product-vm-shard-a-20260907')
DESELECTED = []

def emit(name, value):
    with (OUTPUT / name).open("a", encoding="utf8") as stream:
        stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")

def pytest_deselected(items):
    DESELECTED.extend(item.nodeid for item in items)

def pytest_collection_finish(session):
    for index, item in enumerate(session.items):
        emit("collected-nodes.jsonl", {"index": index, "nodeid": item.nodeid, "module": item.nodeid.split("::", 1)[0]})
    value = {"collected_count": len(session.items), "deselected_count": len(DESELECTED),
             "deselected_nodeids": DESELECTED, "config_args": list(session.config.args),
             "keyword": session.config.option.keyword, "markexpr": session.config.option.markexpr}
    (OUTPUT / "collection-summary.json").write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf8")

def pytest_runtest_logreport(report):
    emit("test-reports.jsonl", {"nodeid": report.nodeid, "phase": report.when,
         "outcome": report.outcome, "duration_seconds": report.duration,
         "expected_failure": str(report.wasxfail) if hasattr(report, "wasxfail") else None})
