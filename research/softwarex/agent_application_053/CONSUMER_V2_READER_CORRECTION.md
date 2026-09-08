# Exact-byte consumer reader correction

The first complete preparation campaign installed the final 0.5.3 wheel and
independently verified five successful fresh MCP seeds. All five consumer plans
then stopped before any model call: their loader read the generated prompts as
text, which normalized the packaged skill's CRLF newlines, and compared that
normalized string with the original unnormalized skill. The recorded prompt
bytes were correct. The original five refusals, seeds, helper sources and entire
sealed campaign are retained unchanged.

`consumer_v2.py` uses explicit v2 plan and attempt schemas and compares the
actual prompt bytes with their expected UTF-8 bytes. It preserves the exact
3359-byte packaged skill and the original 15 prompt files. Regression checks
use those actual CRLF bytes and refuse newline rewriting or added instructions.
The original helper and seed validator remain byte-identical.

`consumer_continuation_v2.py` first reconciles the exact original campaign and
requires five successful seeds, the specific five reader refusals and zero
original model attempts. It records a new campaign, freezes new versioned plans
against the same source roots, existing installed runtime and external
authority, and attempts each original consumer stage once. It does not reseed,
reinstall, alter protected tests, add cases or retry an observed model outcome.
The original issue's production bytes are restored only for the preselected
third stage; that change is recorded outside the sealed original campaign.
