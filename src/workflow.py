"""
Trade-In Agent — end-to-end workflow orchestration.

For every email in data/emails.json:
  1. Email Intake Agent  -> structured TradeInRequest + completeness gate
  2. PARALLEL fan-out:    Stock Agent, Valuation Agent, Scheduling Agent
  3. Reply Agent (fan-in) -> customer-facing reply draft

Usage:
    python workflow.py [email_id ...]     # no args = all emails

Results are written to results/replies.json for evaluation and inspection.
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agents import (
    EmailIntakeAgent, StockAgent, ValuationAgent, SchedulingAgent, ReplyAgent,
    PROJECT_CONNECTION_STRING,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"

BATCH_PROMPT = (
    "You are receiving one incoming customer email. Parse it into the required "
    "JSON structure according to your instructions.

"
    "FROM: {from_name} <{from}>
"
    "SUBJECT: {subject}
"
    "BODY:
{body}"
)


def extract_json(text: str):
    """Extract the first JSON object from an agent response."""
    match = re.search(r"{.*}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def run_stock(stock_agent, intake_result):
    desired = intake_result.get("desired_car") or {}
    prompt = (
        "Check dealership stock for this desired car and, if needed, suggest "
        "alternatives.

"
        "DESIRED_CAR: " + json.dumps(desired)
    )
    return stock_agent.run(prompt)


def run_valuation(valuation_agent, intake_result):
    trade_in = intake_result.get("trade_in_car") or {}
    prompt = (
        "Determine the indicative trade-in value range for this car.

"
        "TRADE_IN_CAR: " + json.dumps(trade_in)
    )
    return valuation_agent.run(prompt)


def run_scheduling(scheduling_agent, intake_result):
    prompt = (
        "Propose appointment slots for this customer request.

"
        "INTAKE: " + json.dumps(intake_result)
    )
    return scheduling_agent.run(prompt)


def process_email(email, intake_agent, stock_agent, valuation_agent,
                  scheduling_agent, reply_agent, executor):
    """Process one email through the full workflow. Returns a result record."""
    email_id = email["email_id"]
    print("Processing", email_id)

    # Step 1 — Intake (sequential gate)
    intake_text = intake_agent.run(BATCH_PROMPT.format(
        from_name=email.get("from_name", ""),
        **{k: email.get(k, "") for k in ("from", "subject", "body")}
    ))
    intake_result = extract_json(intake_text)
    if intake_result is None:
        intake_result = {"parse_error": True, "raw": intake_text}

    # Step 2 — Parallel fan-out (only if the intake gate passed)
    stock_output = None
    valuation_output = None
    scheduling_output = None
    if intake_result.get("parse_error") is not True:
        futures = {
            "stock": executor.submit(run_stock, stock_agent, intake_result),
            "valuation": executor.submit(run_valuation, valuation_agent, intake_result),
            "scheduling": executor.submit(run_scheduling, scheduling_agent, intake_result),
        }
        if intake_result.get("completeness", {}).get("is_sufficient_for_valuation"):
            valuation_output = futures["valuation"].result()
        else:
            # Incomplete request: skip valuation entirely (no guessing)
            futures["valuation"].cancel()
            valuation_output = "SKIPPED_INCOMPLETE_REQUEST"

    # Step 3 — Reply (fan-in)
    reply_prompt = (
        "Write the reply email for this customer request.

"
        "INTAKE_RESULT: " + json.dumps(intake_result) + "

"
        "STOCK_RESULT: " + (stock_output or "SKIPPED") + "

"
        "VALUATION_RESULT: " + (valuation_output or "SKIPPED") + "

"
        "SCHEDULING_RESULT: " + (scheduling_output or "SKIPPED")
    )
    reply_text = reply_agent.run(reply_prompt)

    return {
        "email_id": email_id,
        "intake": intake_result,
        "stock_result": stock_output,
        "valuation_result": valuation_output,
        "scheduling_result": scheduling_output,
        "reply_draft": reply_text,
    }


def main():
    if not PROJECT_CONNECTION_STRING:
        print("PROJECT_CONNECTION_STRING not set. Copy .env.example to .env first.")
        sys.exit(1)

    with open(DATA_DIR / "emails.json", "r", encoding="utf-8") as f:
        emails = json.load(f)["emails"]

    if len(sys.argv) > 1:
        wanted = set(sys.argv[1:])
        emails = [e for e in emails if e["email_id"] in wanted]

    print("=== Trade-In Agent workflow ===")
    print("Creating agents...")
    intake_agent = EmailIntakeAgent(); intake_agent.create()
    stock_agent = StockAgent(); stock_agent.create()
    valuation_agent = ValuationAgent(); valuation_agent.create()
    scheduling_agent = SchedulingAgent(); scheduling_agent.create()
    reply_agent = ReplyAgent(); reply_agent.create()
    print("All agents created.")

    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        for email in emails:
            results.append(process_email(
                email, intake_agent, stock_agent, valuation_agent,
                scheduling_agent, reply_agent, executor))

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / "replies.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=2, ensure_ascii=False)

    print("Done. Results written to results/replies.json")
    for r in results:
        print("
" + "=" * 60)
        print(r["email_id"])
        print(r["reply_draft"])


if __name__ == "__main__":
    main()
