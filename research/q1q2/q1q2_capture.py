"""Pytest evidence capture, independent of both cache implementations."""
import json

COLLECTED = []
REPORTS = []


def pytest_collection_finish(session):
    COLLECTED.extend(item.nodeid for item in session.items)


def pytest_runtest_logreport(report):
    REPORTS.append([report.nodeid, report.when, report.outcome,
                    getattr(report, "wasxfail", None)])


def pytest_sessionfinish(session, exitstatus):
    print("\nQ1Q2_OUTCOMES=" + json.dumps({
        "collected": COLLECTED, "reports": REPORTS, "exit_code": int(exitstatus)},
        sort_keys=True, separators=(",", ":")), flush=True)
