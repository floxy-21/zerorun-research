# ZeroRun configured-task MCP API

The `run_tests` tool accepts only these named arguments:

| Argument | Type | Meaning |
| --- | --- | --- |
| `task` | string, required | Name of a configured ZeroRun task. |
| `root` | string, optional | Repository path; defaults to the server working directory. |
| `verify` | boolean, optional; default `false` | When false, apply normal reuse eligibility: reuse a matching successful result when eligible, otherwise execute fresh. When true, execute the configured task fresh and compare with a matching stored success when one exists. Without a matching entry, execution follows the fresh miss path. |

Choose arguments from the requested validation need and this interface. Do not
invent additional argument names. Whether execution or reuse actually occurred
must be determined from the returned result, not from the request alone.

This result-only interface does not restore generated files or replay cached
diagnostic streams. Fresh executions return bounded output tails, not a promise
of a complete transcript; empty output does not imply diagnostic text exists.

Execution requires the existing reviewed v2 task, exact external operator
authority, and already-present pinned runtime. This API card does not authorize
a repository, change approvals, or permit acquiring a runtime. A protocol or
argument error does not establish successful validation or fresh execution.
A freshly executed failing test is fresh evidence of failure, not success.
