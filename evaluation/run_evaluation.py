"""
Trade-In Agent — evaluation runner.

Runs the workflow over data/emails.json (if not already done), then compares
the results in results/replies.json against evaluation/eval_cases.json.

Checks per case (deterministic part of the evaluation):
  - intake completeness decision matches expectation
  - valuation was skipped exactly when expected
  - value bounds found in the valuation output match the expected bounds
  - stock status matches expectation
  - reply mentions the expected key facts (value range / missing fields /
    on-site valuation)

LLM-quality dimensions (groundedness, coherence, fluency, task adherence,
tool usage) are assessed with the Foundry evaluation framework against this
same dataset; see docs/observability.md and docs/production_readiness.md.
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "results" / "replies.json"
CASES_PATH = Path(__file__).resolve().parent / "eval_cases.json"


def load_results():
    if not RESULTS_PATH.exists():
        print("results/replies.json not found. Run 'python src/workflow.py' first.")
        sys.exit(1)
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        return {r["email_id"]: r for r in json.load(f)["results"]}


def check_case(case, result):
    errors = []
    exp = case["expected"]
    intake = result.get("intake") or {}
    comp = intake.get("completeness") or {}

    if comp.get("is_sufficient_for_valuation") != exp.get("is_sufficient_for_valuation"):
        errors.append("completeness decision mismatch")

    if exp.get("valuation_skipped"):
        val = result.get("valuation_result")
        if val != "SKIPPED_INCOMPLETE_REQUEST":
            errors.append("valuation was not skipped")
    else:
        val = result.get("valuation_result") or ""
        if "lower_bound_eur" in exp:
            m = re.search(r"([0-9]{4,6})", val)
            if not m:
                errors.append("no valuation figures found")
            else:
                if str(exp["valuation_lower_bound_eur"]) not in val:
                    errors.append("lower bound not found in output")
                if str(exp["valuation_upper_bound_eur"]) not in val:
                    errors.append("upper bound not found in output")

    stock = result.get("stock_result") or ""
    if "stock_status" in exp and exp["stock_status"] not in stock:
        errors.append("stock status '" + exp["stock_status"] + "' not found in stock output")

    reply = result.get("reply_draft") or ""
    if exp.get("reply_mentions_onsite_valuation"):
        if not re.search(r"on-site|on site|in person", reply, re.IGNORECASE):
            errors.append("reply does not mention on-site valuation")
    if comp.get("is_sufficient_for_valuation") is False:
        if not re.search(r"mileage|year|licence|license|details", reply, re.IGNORECASE):
            errors.append("reply does not request missing information")

    return errors


def main():
    results = load_results()
    with open(CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    passed = 0
    failed = 0
    print("=== Trade-In Agent evaluation ===")
    for case in cases:
        result = results.get(case["email_id"])
        if not result:
            print("MISSING ", case["email_id"])
            failed += 1
            continue
        errors = check_case(case, result)
        if errors:
            failed += 1
            print("FAIL    ", case["email_id"], "-", "; ".join(errors))
        else:
            passed += 1
            print("PASS    ", case["email_id"], "(", case["expected_path"], ")")

    total = passed + failed
    print()
    print("Result: " + str(passed) + "/" + str(total) + " cases passed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
