"""Validate completed exact-main revision evidence without weakening release gates."""
from pathlib import Path

from research.sqj.analyze_campaign import read_json, require, digest, validate_record
from research.sqj.analyze_comparison import validate_source
from research.sqj.run_frozen_campaign import WORKLOADS

SQJ = Path(__file__).resolve().parent
MAIN = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
ENGINE = "4e171ba936e0907f14e5896821da0741af69c800aa12043cab5dd4bbfd20e698"
PRODUCER = "ec7e2f615c20721805ac139275fdadb34c4029f934956266918d8feaa4a1f3c3"


def analyze(path=None):
    path = path or SQJ / "evidence/github-86f4228/revision-final/product-generalization-five-repo-receipt.json"
    raw = read_json(path)
    require(raw["engine_sha"] == MAIN and raw["schema"] == "zerorun.product-generalization.aggregate.v1", "wrong current revision identity")
    require(raw["reference_gate"] == {"min_compute_efficiency": 5.0, "min_p95_reduction_percent": 50.0}, "current release gate changed")
    require([r["workload"] for r in raw["workloads"]] == [w[0] for w in WORKLOADS], "missing or reordered current revision subject")
    rows = []
    for record, item in zip(raw["workloads"], WORKLOADS, strict=True):
        validate_source(record["source_identity"]["pre"], SQJ / "source-ci-final")
        result = validate_record(record, item, expected_base=MAIN, expected_engine=ENGINE, expected_producer=PRODUCER)
        rows.append(result)
    require(raw["all_safety_pass"] is all(r["safety_checks_pass"] for r in rows), "aggregate safety differs from raw cases")
    require(raw["general_5x_claim_supported_by_this_suite"] is all(r["product_gate_pass"] for r in rows), "aggregate product gate differs")
    return {"schema": "zerorun.current-revision-analysis.v1", "rows": rows, "main_commit": MAIN,
            "engine_sha256": ENGINE, "producer_sha256": PRODUCER, "raw_sha256": digest(path),
            "completed_subjects": len(rows), "comparisons": sum(r["observations"] for r in rows),
            "all_product_gates_pass": all(r["product_gate_pass"] for r in rows),
            "separate_hardware_from_controlled_experiment": True, "not_pooled_with_controlled_requests": True}


if __name__ == "__main__":
    import json
    print(json.dumps(analyze(), indent=2, sort_keys=True))
