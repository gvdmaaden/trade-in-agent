# Production Readiness Plan

## From mock data to live sources

| Prototype | Production | Migration effort |
|-----------|------------|------------------|
| `data/rdw_lookup.json` | RDW Open Data SODA API (free, CC-0) | swap function body, same schema |
| `data/valuation_guide.json` | licensed valuation feed (ANWB koerslijst, Transwise, Eurotax) | contract + periodic import |
| `data/inventory.json` | dealership DMS / website stock API | REST integration |
| `data/calendar.json` | Microsoft Graph (read-only calendar view) | OAuth app registration |
| `data/emails.json` | inbox trigger (Logic App / Graph subscription on mail folder) | event-driven trigger |

The ANWB koerslijst has **no public API**; scraping it is fragile and legally
sensitive. The production plan is therefore a licensed feed — the mock file was
designed with exactly that schema so migration is a single function-body swap.

## Consistency and reliability

- **Deterministic tools**: value ranges, stock status and slot calculation are
  pure table lookups / computations — identical input yields identical output.
- **No slot allocation**: the agent only proposes; the customer confirms via
  phone/email. No write access to the calendar, no double-booking risk.
- **Versioned agents**: every deploy creates a new agent version in Foundry;
  rollback is a version switch.
- **Prompt changes via PR**: system prompts live in source control; every change
  must pass the evaluation set before merge (CI gate).

## Safety and governance (GDPR)

- Trade-in emails contain personal data (name, email, license plate).
  - Data minimisation: only fields needed for the workflow are extracted.
  - Retention: processed emails and drafts are deleted after X days
    (configurable; default 30).
  - License plates are personal data under Dutch practice; RDW lookups use the
    official open-data endpoint only.
- **Human-in-the-loop**: the workflow produces a *draft* reply. A dealership
  employee reviews and approves before sending. No autonomous outbound email
  in the prototype.
- **No hallucinated values**: hard rule in every agent prompt, enforced by
  evaluation (groundedness checks on every value in every reply).

## Deployment roadmap

1. **Prototype** (this repo): mock data, batch runner, evaluation set
2. **Pilot**: connect RDW live + real inbox folder; drafts reviewed by 1 employee
3. **Hardening**: CI/CD with evaluation gates, dashboards, alerting
4. **Production**: full DMS/calendar integration, gradual rollout with
   human-in-the-loop approval retained

## Evaluation in the lifecycle

- The 15-case set in `evaluation/eval_cases.json` is the regression baseline.
- Every prompt/model/tool change re-runs the set; a regression blocks release.
- Human feedback (edited/approved drafts) feeds new cases weekly.
