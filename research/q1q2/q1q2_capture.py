"""Pytest evidence capture, independent of both cache implementations."""
import json
import hashlib

COLLECTED = []
REPORTS = []


def pytest_collection_finish(session):
    COLLECTED.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    REPORTS.append([report.nodeid, report.when, report.outcome,
                    getattr(report, "wasxfail", None)])


def pytest_sessionfinish(session, exitstatus):
    payload = {"collected": sorted(COLLECTED), "reports": sorted(REPORTS, key=lambda r: (r[0], r[1])),
               "exit_code": int(exitstatus)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    print("\nQ1Q2_OUTCOMES=" + encoded, flush=True)
    # ZeroRun's actual MCP API intentionally returns only a 4,000-byte tail.
    # Keep a compact evidence digest inside that tail; full-tree oracle output
    # retains the complete node/phase record to which it can be compared.
    print("Q1Q2_SUMMARY=" + json.dumps({"sha256": hashlib.sha256(encoded.encode()).hexdigest(),
          "collected_count": len(COLLECTED), "reports_count": len(REPORTS),
          "exit_code": int(exitstatus)}, sort_keys=True), flush=True)
