# Focused implementation checks

The final Windows receipt is at
`docs/evidence/softwarex-regression/windows-20260906-readonly-hit-final/junit.xml`.
The following exact test cases are recorded as passing (no skipped, failure,
or error element). They describe controlled implementation tests, not an
additional upstream corpus, actual Codex episode, or production review.

| Behavior | Source file | Exact test identifier |
| --- | --- | --- |
| Miss, publication, hit, and source-edit invalidation | `tests/test_pytest_runtime_adapter.py` | `test_node_cache_miss_publish_hit_and_source_edit_invalidation` |
| Source recheck before cached response | `tests/test_pytest_runtime_adapter.py` | `test_verified_action_snapshot_rechecks_source_before_return` |
| Reordered shared tests force fresh execution | `tests/test_pytest_per_node_alignment.py` | `test_reordered_shared_nodes_fail_closed_to_whole_target_fresh` |
| Dynamic import remains unresolved | `tests/test_pytest_profiler_adversarial.py` | `test_dynamic_builtins_import_remains_fresh_required` |
| Replaced monitoring callback is detected | `tests/test_pytest_profiler_adversarial.py` | `test_monitoring_detects_transient_py_resume_callback_replacement` |
| Authenticated whole-file hit neither stages nor executes | `tests/test_hermetic.py` | `test_readonly_whole_task_hit_does_not_stage_or_execute` |
| Changed source after authenticated lookup is rejected | `tests/test_hermetic.py` | `test_readonly_hit_rechecks_source_after_authenticated_lookup` |
| Force and verification never use the read-only shortcut | `tests/test_hermetic.py` | `test_force_and_verify_bypass_readonly_hit_optimization` |

The table is illustrative rather than exhaustive. Some tests simulate runtime
components; their passing status is not equivalent to the real Docker campaign.
The monitoring case requires an interpreter with the monitoring API; it must
remain skipped, not counted as passing, on unsupported Python versions.

The manuscript generator validates all eight named Windows JUnit results before
rendering the corresponding paragraph. Full host logs retain other skipped
cases and the earlier two research-fixture failures before their correction.

Current-core Linux CI passed on Python 3.10, 3.11, 3.12, 3.13, and 3.14. The
raw logs identify the exact commit; their primary test counts and subtest counts
are parsed separately. The separate 100-repository integration receipt is
rechecked by the existing strict commercial-smoke validator, including source
commit, frozen selection, skill identity, MCP proofs, and unchanged safe-refusal
state. That smoke is not a performance or customer-adoption test.
