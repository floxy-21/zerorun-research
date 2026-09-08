# Consumer prelaunch correction

The first detached consumer campaign launch on 8 September 2026 stopped while
importing its portable helper package. The transport package omitted the
unchanged `run_public_lifecycle.py`, imported by the existing diagnostic module.
The original package, launch receipt and complete raw stdout/stderr are retained.
This refusal occurred before the campaign protocol, installation, authority,
MCP seed or any model attempt was created. A new package includes and binds the
transitive module and performs an actual import preflight before execution.

While resolving that packaging refusal, static review identified another
pre-execution issue: the seed checker reused a synthetic silent-task assertion
that both diagnostic tails must be empty. Actual successful pytest tasks can
return nonempty tails. The corrected checker retains the exact request,
server/version, structured/text agreement, successful fresh-execution status,
exit code, cache key, source, manifest and installed-runtime checks. It validates
the real interface's bounded string tails and retains their exact raw contents.
It does not rewrite a response, convert a hit into fresh execution or suppress
an adverse result.

Both corrections were made before the first seed or consumer invocation. The
five eligible original agent-produced source states, their protected tests,
the separately preserved unsuccessful sixth case, image, wheel, prompts,
three-stage order and one-attempt-per-stage limits are unchanged. These are
operator preparation corrections, not retries of an observed model outcome.
