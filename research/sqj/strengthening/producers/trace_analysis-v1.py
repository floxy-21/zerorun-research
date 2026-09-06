"""Descriptive analysis of recorded agent actions; never executes their contents.

Exact command repetition is not a cache hit. No recorded action establishes that
the entire execution environment or source tree was unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
from typing import Any


class InvalidEvidence(ValueError):
    """A record cannot be interpreted without guessing."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise InvalidEvidence(f"nonfinite_json:{value}")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise InvalidEvidence("nonfinite_json_float")
    return result


def strict_json(content: str | bytes) -> Any:
    try:
        return json.loads(content, object_pairs_hook=_unique_object, parse_constant=_constant, parse_float=_finite_float)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidEvidence("invalid_json") from exc


def recognize_command(command: str) -> dict[str, Any]:
    """Recognize a deliberately small shell grammar, without running a shell.

Supported means syntactically a direct pytest invocation, not successful,
hermetic, safe to execute, or cacheable. Keep exact command bytes as repeat keys.
    """
    result: dict[str, Any] = {"supported": False, "reason": "not_direct_pytest", "cwd": None}
    if not isinstance(command, str):
        return result | {"reason": "command_not_string"}
    if any(ord(char) < 32 or ord(char) == 127 for char in command):
        return result | {"reason": "control_character_or_multiline"}
    # Conservative even inside quotes: no expansion, redirection, or globbing
    # needs to be interpreted by this observational parser.
    if any(char in command for char in "`$;|<>#*?{}[]~()"):
        return result | {"reason": "shell_operator_expansion_or_glob"}
    body = command.strip()
    if "&&" in body:
        parts = body.split("&&")
        if len(parts) != 2:
            return result | {"reason": "complex_shell_chain"}
        try:
            prefix = shlex.split(parts[0], posix=True)
        except ValueError:
            return result | {"reason": "invalid_shell_quoting"}
        if len(prefix) != 2 or prefix[0] != "cd" or not prefix[1] or prefix[1].startswith("-"):
            return result | {"reason": "non_cd_shell_chain"}
        result["cwd"] = prefix[1]
        body = parts[1].strip()
    if "&" in body or "&" in str(result["cwd"]):
        return result | {"reason": "shell_operator_expansion_or_glob"}
    try:
        tokens = shlex.split(body, posix=True)
    except ValueError:
        return result | {"reason": "invalid_shell_quoting"}
    if not tokens:
        return result | {"reason": "empty_command"}
    if tokens[0] == "env" or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", tokens[0]):
        return result | {"reason": "environment_override"}
    if tokens[0] == "pytest":
        arguments = tokens[1:]
    elif re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", tokens[0]) and tokens[1:3] == ["-m", "pytest"]:
        arguments = tokens[3:]
    else:
        return result
    if any(arg in {"--help", "-h", "--version", "-V", "--collect-only", "--co"} for arg in arguments):
        return result | {"reason": "pytest_non_execution_request", "argv": tokens}
    if "-" in arguments:
        return result | {"reason": "stdin_or_invalid_dash_argument", "argv": tokens}
    return result | {"supported": True, "reason": None, "argv": tokens}


_EXIT = re.compile(r"^\[(?:The command completed with exit code (-?\d+)\.|Command finished with exit code (-?\d+))\]$", re.MULTILINE)
_SUMMARY = re.compile(r"^(?P<counts>\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)(?:, \d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?))*) in \d+(?:\.\d+)?s(?: .*)?$")


def observation_evidence(content: str) -> dict[str, Any]:
    codes = [int(left or right) for left, right in _EXIT.findall(content)]
    distinct = sorted(set(codes))
    summaries = []
    for line in content.splitlines():
        normalized = line.strip().strip("=").strip()
        match = _SUMMARY.fullmatch(normalized)
        if match:
            counts = {}
            for amount, category in re.findall(r"(\d+) ([a-z]+)", match["counts"]):
                counts[category] = int(amount)
            summaries.append(counts)
    summary = summaries[-1] if summaries else None
    shell_code = distinct[0] if len(distinct) == 1 else None
    return {
        "recorded_shell_exit_code": shell_code,
        "exit_markers": codes,
        "ambiguous_exit_markers": len(distinct) > 1,
        "recorded_pytest_summary": summary,
        "pytest_summary_candidates": len(summaries),
        "shell_zero_with_failed_or_error_summary": bool(shell_code == 0 and summary and (summary.get("failed", 0) or summary.get("error", 0) or summary.get("errors", 0))),
        "observation_clipped": any(marker in content.lower() for marker in ("<response clipped>", "[output truncated", "(...truncated)", "[... observation truncated due to length ...]")),
    }


def editor_status(command: str, observation: str) -> str:
    if not isinstance(command, str):
        return "editor_unknown_operation"
    if command == "view":
        return "editor_view"
    if command not in {"create", "str_replace", "insert", "undo_edit"}:
        return "editor_unknown_operation"
    if re.match(r"\s*(?:ERROR\b|Error\b|Traceback\b)", observation):
        return "editor_failed_attempt"
    if observation.startswith("File created successfully at:") or re.match(r"The file .+ has been edited\.", observation):
        return "editor_reported_mutation"
    # Do not invent success from an arbitrary observation or undocumented undo.
    return "editor_outcome_unknown"


def analyze_episode(row: dict[str, Any], row_index: int) -> dict[str, Any]:
    if not isinstance(row, dict) or not all(isinstance(row.get(key), str) and row[key] for key in ("trajectory_id", "instance_id", "repo")):
        raise InvalidEvidence("missing_episode_identity")
    messages = row.get("trajectory")
    if not isinstance(messages, list):
        raise InvalidEvidence("trajectory_not_list")
    calls: list[dict[str, Any]] = []
    call_ids: set[str] = set()
    observations: dict[str, tuple[int, dict[str, Any]]] = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or message["role"] not in {"system", "user", "assistant", "tool"}:
            raise InvalidEvidence("invalid_message")
        nested = message.get("tool_calls")
        if nested is not None and not isinstance(nested, list):
            raise InvalidEvidence("tool_calls_not_list")
        if nested and message["role"] != "assistant":
            raise InvalidEvidence("nonassistant_tool_calls")
        for call in nested or []:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
                raise InvalidEvidence("missing_call_id")
            if call["id"] in call_ids:
                raise InvalidEvidence("duplicate_call_id")
            call_ids.add(call["id"])
            function = call.get("function")
            if call.get("type") != "function" or not isinstance(function, dict) or not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                raise InvalidEvidence("invalid_function_call")
            arguments = strict_json(function["arguments"])
            if not isinstance(arguments, dict):
                raise InvalidEvidence("arguments_not_object")
            calls.append({"call_id": call["id"], "name": function["name"], "arguments": arguments, "message_index": message_index, "call_index": len(calls)})
        if message["role"] == "tool":
            identifier = message.get("tool_call_id")
            if not isinstance(identifier, str) or not identifier:
                raise InvalidEvidence("missing_observation_call_id")
            if identifier in observations:
                raise InvalidEvidence("duplicate_observation_id")
            if not isinstance(message.get("content"), str):
                raise InvalidEvidence("observation_not_string")
            observations[identifier] = (message_index, message)
    missing_observations = call_ids - set(observations)
    terminal_finish = None
    if calls and calls[-1]["name"] == "finish" and calls[-1]["message_index"] == len(messages) - 1 and missing_observations == {calls[-1]["call_id"]}:
        # OpenHands terminates on finish without a follow-up tool observation.
        # This narrow terminal exception never covers execution or editing.
        terminal_finish = calls[-1]["call_id"]
    if set(observations) - call_ids or missing_observations - ({terminal_finish} if terminal_finish else set()):
        raise InvalidEvidence("unmatched_call_or_observation")
    for call in calls:
        if call["call_id"] == terminal_finish:
            call["observation"] = ""
            continue
        position, observation = observations[call["call_id"]]
        if position <= call["message_index"]:
            raise InvalidEvidence("observation_precedes_call")
        if observation.get("name") is not None and observation["name"] != call["name"]:
            raise InvalidEvidence("observation_name_mismatch")
        call["observation"] = observation["content"]

    counters: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    events = []
    pairs = []
    previous_commands: dict[str, dict[str, Any]] = {}
    for call in calls:
        counters["tool_calls"] += 1
        counters["terminal_finish_without_observation"] += int(call["call_id"] == terminal_finish)
        name, arguments, observation = call["name"], call["arguments"], call["observation"]
        event = {key: call[key] for key in ("call_id", "call_index", "message_index", "name")}
        if name == "execute_bash":
            counters["bash_calls"] += 1
            command = arguments.get("command")
            recognized = recognize_command(command)
            input_value = arguments.get("is_input")
            if not (input_value is None or input_value is False or input_value == "false"):
                recognized = recognized | {"supported": False, "reason": "interactive_stdin_input"}
            if set(arguments) - {"command", "timeout", "is_input"}:
                recognized = recognized | {"supported": False, "reason": "unknown_bash_arguments"}
            evidence = observation_evidence(observation)
            mention = isinstance(command, str) and re.search(r"\bpytest\b", command) is not None
            counters["pytest_mention_calls"] += int(mention)
            counters["direct_pytest_calls"] += int(recognized["supported"])
            counters["shell_zero_with_failed_or_error_summary"] += int(evidence["shell_zero_with_failed_or_error_summary"])
            counters["ambiguous_exit_markers"] += int(evidence["ambiguous_exit_markers"])
            counters["missing_shell_exit_markers"] += int(not evidence["exit_markers"])
            counters["clipped_observations"] += int(evidence["observation_clipped"])
            if not recognized["supported"]:
                unsupported[recognized["reason"]] += 1
            # Even a syntactically direct test can write files or change state.
            barrier = "test_execution_effects_unmeasured" if recognized["supported"] else "bash_effects_unknown"
            event |= {"command": command, "recognition": recognized, "pytest_mention": mention, "barrier": barrier, **evidence}
            if recognized["supported"]:
                counters["direct_pytest_with_recorded_exit_zero"] += int(evidence["recorded_shell_exit_code"] == 0)
                summary = evidence["recorded_pytest_summary"]
                counters["direct_pytest_with_nonzero_pass_count"] += int(bool(summary and summary.get("passed", 0) > 0))
                counters["direct_pytest_without_positive_pass_summary"] += int(not summary or summary.get("passed", 0) == 0)
                if command in previous_commands:
                    prior = previous_commands[command]
                    intervening = events[prior["call_index"] + 1:]
                    barriers = [entry for entry in intervening if entry["barrier"] is not None]
                    pairs.append({
                        "trajectory_id": row["trajectory_id"], "row_index": row_index,
                        "prior_call_id": prior["call_id"], "current_call_id": call["call_id"],
                        "prior_call_index": prior["call_index"], "current_call_index": call["call_index"],
                        "exact_command": command,
                        "intervening_barrier_counts": dict(sorted(Counter(item["barrier"] for item in barriers).items())),
                        "intervening_barriers": [{key: item[key] for key in ("call_id", "call_index", "barrier")} for item in barriers],
                        "source_unchanged_established": False,
                        "cache_hit_established": False,
                        "prior_execution_effects": "unmeasured_even_without_intervening_actions",
                    })
                previous_commands[command] = event
        elif name == "str_replace_editor":
            classification = editor_status(arguments.get("command"), observation)
            counters[classification] += 1
            event |= {"editor_operation": arguments.get("command"), "editor_path": arguments.get("path"), "editor_classification": classification,
                      "barrier": None if classification == "editor_view" else classification}
        elif name in {"think", "finish"}:
            counters["recorded_nonexecution_calls"] += 1
            event["barrier"] = None
        else:
            counters["unknown_tool_calls"] += 1
            event["barrier"] = "unknown_tool_effects"
        events.append(event)
    counters["exact_command_repeat_pairs"] = len(pairs)
    counters["distinct_direct_pytest_commands"] = len(previous_commands)
    for field in ("tool_calls", "bash_calls", "pytest_mention_calls", "direct_pytest_calls", "direct_pytest_with_recorded_exit_zero", "direct_pytest_with_nonzero_pass_count", "direct_pytest_without_positive_pass_summary"):
        counters.setdefault(field, 0)
    return {"row_index": row_index, "trajectory_id": row["trajectory_id"], "instance_id": row["instance_id"], "repo": row["repo"],
            "recorded_agent_exit_status": row.get("exit_status"), "recorded_resolved": row.get("resolved"),
            "totals": dict(sorted(counters.items())), "unsupported_bash_reasons": dict(sorted(unsupported.items())),
            "events": events, "candidate_repeat_pairs": pairs}


LIMITATIONS = [
    "This is descriptive analysis of recorded actions, not command execution or an AI-agent rerun.",
    "A direct pytest signature, recorded exit zero, nonzero passed count, and agent resolved flag are separate observations.",
    "Repeated exact command strings do not establish cache hits, equivalent input states, environment stability, or safe reuse.",
    "Every bash action has unmeasured effects; even tests and apparently read-only shell commands can mutate hidden state.",
    "Recorded editor success is an observation, not independently reconstructed source; failed and unknown attempts remain barriers.",
    "Tool-observation clipping alone does not exclude an episode; HF cell truncation and invalid/incomplete evidence remain explicit exclusions.",
    "Recorded shell status and pytest summaries may differ because of pipelines; neither is fresh validation.",
    "Observed timings are not used to claim cache savings or autonomous-task improvement.",
    "Raw API byte hashes bind the downloaded snapshot; dataset-server row endpoints are not assumed revision-specific.",
    "A final finish call may have no tool response because it terminates OpenHands; this exception never applies to bash/editor calls.",
]


def analyze_collection(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    collection_raw = (directory / "collection.json").read_bytes()
    selection_raw = (directory / "selection.json").read_bytes()
    collection = strict_json(collection_raw)
    selection = strict_json(selection_raw)
    if not isinstance(collection, dict) or not isinstance(collection.get("entries"), list):
        raise InvalidEvidence("collection_entries_missing")
    if not isinstance(selection, dict):
        raise InvalidEvidence("selection_not_object")
    selected = selection.get("selected_indices")
    population = selection.get("population_rows")
    if type(population) is not int or population <= 0 or not isinstance(selected, list) or any(type(index) is not int or not 0 <= index < population for index in selected) or len(set(selected)) != len(selected):
        raise InvalidEvidence("invalid_frozen_selection")
    if collection.get("selected_indices") != selected or collection.get("population_rows") != population:
        raise InvalidEvidence("selection_collection_mismatch")
    entries = collection["entries"]
    indices = [entry.get("row_index") for entry in entries if isinstance(entry, dict)]
    if len(indices) != len(entries) or any(type(index) is not int or index < 0 for index in indices) or len(set(indices)) != len(indices):
        raise InvalidEvidence("invalid_or_duplicate_manifest_row_index")
    if set(indices) != set(selected):
        raise InvalidEvidence("manifest_does_not_match_frozen_selection")
    episodes, unavailable = [], []
    seen_trajectories: set[str] = set()
    for entry in sorted(entries, key=lambda item: item["row_index"]):
        index = entry["row_index"]
        if entry.get("status") != "ok":
            unavailable.append({"row_index": index, "reason": "collection_status_not_ok", "status": entry.get("status")})
            continue
        try:
            relative = entry.get("path")
            if not isinstance(relative, str):
                raise InvalidEvidence("manifest_path_missing")
            file = (directory / relative).resolve()
            if Path(relative).is_absolute() or not file.is_relative_to(directory) or file == directory / "collection.json":
                raise InvalidEvidence("manifest_path_outside_raw_directory")
            raw = file.read_bytes()
            if type(entry.get("bytes")) is not int or entry["bytes"] != len(raw):
                raise InvalidEvidence("raw_byte_length_mismatch")
            if entry.get("sha256") != hashlib.sha256(raw).hexdigest():
                raise InvalidEvidence("raw_sha256_mismatch")
            response = strict_json(raw)
            if not isinstance(response, dict) or not isinstance(response.get("rows"), list) or len(response["rows"]) != 1:
                raise InvalidEvidence("response_not_exactly_one_row")
            if response.get("partial") is not False:
                raise InvalidEvidence("partial_or_unknown_response")
            if type(response.get("num_rows_total")) is not int or response["num_rows_total"] != population:
                raise InvalidEvidence("response_population_mismatch")
            wrapped = response["rows"][0]
            if not isinstance(wrapped, dict) or type(wrapped.get("row_idx")) is not int or wrapped["row_idx"] != index:
                raise InvalidEvidence("row_index_mismatch")
            if "truncated_cells" not in wrapped or not isinstance(wrapped["truncated_cells"], list):
                raise InvalidEvidence("missing_truncation_metadata")
            if wrapped["truncated_cells"]:
                raise InvalidEvidence("hf_truncated_cells")
            episode = analyze_episode(wrapped.get("row"), index)
            if episode["trajectory_id"] in seen_trajectories:
                raise InvalidEvidence("duplicate_trajectory_id")
            seen_trajectories.add(episode["trajectory_id"])
            episodes.append(episode)
        except (InvalidEvidence, OSError) as exc:
            unavailable.append({"row_index": index, "reason": str(exc) if isinstance(exc, InvalidEvidence) else "raw_file_unavailable"})
    aggregate: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    repositories: dict[str, dict[str, Any]] = {}
    for episode in episodes:
        aggregate.update(episode["totals"])
        unsupported.update(episode["unsupported_bash_reasons"])
        repo = repositories.setdefault(episode["repo"], {"episodes": 0, "instance_ids": set(), "totals": Counter()})
        repo["episodes"] += 1
        repo["instance_ids"].add(episode["instance_id"])
        repo["totals"].update(episode["totals"])
    for repo in repositories.values():
        repo["instance_ids"] = sorted(repo["instance_ids"])
        repo["totals"] = dict(sorted(repo["totals"].items()))
    return {"schema_version": 1, "analysis_kind": "recorded_action_descriptive_only", "selected_rows": len(selected),
            "candidate_repeat_scope": "supported_direct_pytest_signatures_with_identical_raw_command_within_one_episode",
            "provenance": {"collection_sha256": hashlib.sha256(collection_raw).hexdigest(),
                           "selection_sha256": hashlib.sha256(selection_raw).hexdigest(),
                           "analyzer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           "population_rows": population},
            "valid_episodes": len(episodes), "unavailable_rows": unavailable,
            "invalid_coverage_reasons": dict(sorted(Counter(item["reason"] for item in unavailable).items())),
            "repos": dict(sorted(repositories.items())), "totals": dict(sorted(aggregate.items())),
            "unsupported_bash_reasons": dict(sorted(unsupported.items())), "episodes": episodes,
            "candidate_repeat_pairs": [pair for episode in episodes for pair in episode["candidate_repeat_pairs"]],
            "limitations": LIMITATIONS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    report = analyze_collection(options.directory)
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
