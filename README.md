# Trade-In Agent

A multi-agent AI solution that automatically handles incoming car **trade-in request emails** for a car dealership — built with the Microsoft Foundry / Azure AI Agents SDK pattern for the Agent-a-thon (*From Prototype to Production*).

> Implementation pattern (specialised agents, function tools, mock data, evaluation set) is inspired by the [Microsoft FrontierWeekHack](https://github.com/microsoft/FrontierWeekHack) labs. All business logic, scenario design and data are original to this project.

## Business Scenario

A car dealership receives dozens of trade-in emails per day. Each email mentions:

1. **The desired car** — the car the customer wants to buy from the dealership (availability is checked against stock)
2. **The trade-in car** — the customer's own car, whose indicative value must be determined (lower/upper bound)

Today every email is read and answered manually. This solution automates the full flow:

```text
Incoming email -> Intake (parse + completeness gate)
                     |
        +------------+------------+------------------+
        |            |            |                  |
   Stock Agent   Valuation Agent  Scheduling Agent   (parallel fan-out)
        |            |            |
        +------------+------------+
                     |
               Reply Agent  ----> Customer-facing reply draft (human-in-the-loop)
```

## The Four Scenario Paths

| Path | Condition | Behaviour |
|------|-----------|-----------|
| 1 | Complete request + desired car in stock | Availability + trade-in value range + 3 proposed appointment slots |
| 2 | Complete request + desired car NOT in stock | "No longer available" + alternatives from stock + value range + slots |
| 3 | Incomplete request | Valuation is **skipped** (no guessing) + friendly request for missing fields |
| 4 | Edge cases | RDW lookup fails (fallback to email data), car not in valuation guide (on-site valuation), etc. |

## Agents

| Agent | Responsibility | Tools |
|-------|----------------|-------|
| **Email Intake Agent** | Parse email into structured JSON, completeness check, routing decision | — |
| **Stock Agent** | Check availability of the desired car, suggest alternatives | `check_inventory`, `suggest_alternatives` |
| **Valuation Agent** | Determine indicative trade-in value range (lower/upper bound) | `rdw_lookup`, `lookup_value_range` |
| **Scheduling Agent** | Propose free appointment slots (next 7 days, 09:00-17:00, 60 min, read-only) | `find_available_slots` |
| **Reply Agent** | Compose the final customer-facing email, path-dependent | — |

## Key Design Decisions

- **Parallel fan-out**: Stock, Valuation and Scheduling run concurrently once the intake gate passes; the Reply Agent is the fan-in point.
- **No slot allocation**: the agent only *proposes* slots; the customer must confirm by phone/email. This avoids double-booking and keeps calendar access **read-only**.
- **Grounded valuation**: value ranges come from a deterministic table lookup (`data/valuation_guide.json`), never from LLM reasoning. The model is explicitly instructed never to state a value outside tool results.
- **Anti-hallucination gate**: if the trade-in car data is incomplete, valuation is skipped entirely instead of guessed.
- **Mock data with production schemas**: `rdw_lookup.json` mirrors the [RDW Open Data SODA API](https://opendata.rdw.nl) response shape; `valuation_guide.json` mirrors a licensed valuation feed (e.g. ANWB koerslijst). Production migration is a single function-body swap.
- **Human-in-the-loop**: the workflow produces a **draft** reply; a dealership employee approves before sending (see `docs/production_readiness.md`).

## Getting Started

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your Foundry project connection string
cd src
python agents.py            # smoke test: create agents and parse one email
python workflow.py          # process all emails in data/emails.json
python workflow.py EMAIL-001 EMAIL-005   # or a selection
```

Replies are written to `results/replies.json`.

## Evaluation

```bash
cd src
python ../evaluation/run_evaluation.py
```

15 curated test emails cover all four paths, including edge cases (RDW lookup failure, untaxatable vehicles, missing fields). Each case defines expected values (path, stock status, value bounds, slot count) and is scored on groundedness, task adherence, coherence, fluency and tool usage.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — agent diagram, data contracts, scenario paths
- [`docs/observability.md`](docs/observability.md) — tracing and monitoring strategy
- [`docs/production_readiness.md`](docs/production_readiness.md) — governance, GDPR, reliability, production migration plan

## Repository Structure

```text
trade-in-agent/
├── README.md
├── requirements.txt
├── .env.example
├── src/
│   ├── agents.py          # 5 agent definitions + function tools
│   └── workflow.py        # orchestration: intake gate -> parallel fan-out -> reply
├── data/
│   ├── emails.json        # 15 mock incoming emails (doubles as evaluation input)
│   ├── inventory.json     # dealership stock
│   ├── valuation_guide.json  # ANWB-style value ranges
│   ├── calendar.json      # mock agenda for slot calculation
│   └── rdw_lookup.json    # mock RDW Open Data responses per license plate
├── evaluation/
│   ├── eval_cases.json    # expected outcomes per email
│   └── run_evaluation.py  # compares agent output vs expected
└── docs/
    ├── architecture.md
    ├── observability.md
    └── production_readiness.md
```
