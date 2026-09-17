# Architecture

## Agent Topology

```text
                      ┌─────────────────────┐
   Incoming email ──> │ Email Intake Agent  │  parse + completeness gate
                      └──────────┬──────────┘
               sufficient?       │
        ┌──────── yes ──────────┼───────────────────── no ────────────┐
        ▼                       ▼                                    │
┌───────────────┐   ┌───────────────────┐   ┌──────────────────┐      │
│ Stock Agent   │   │ Valuation Agent   │   │ Scheduling Agent │      │
│ (parallel)    │   │ (parallel)        │   │ (parallel)       │      │
└───────┬───────┘   └─────────┬─────────┘   └────────┬─────────┘      │
        └─────────────┬───────┴──────────────────────┘                │
                      ▼                                               ▼
              ┌──────────────┐                              ┌──────────────┐
              │ Reply Agent  │ <─── partial results ──────── │ Reply Agent  │
              └──────┬───────┘                              └──────┬───────┘
                     ▼                                             ▼
          Customer-facing reply draft                    Customer-facing
          (human-in-the-loop approval)                   reply draft
```

- The three specialist agents run in a **parallel fan-out** (ThreadPoolExecutor);
  they share no data dependencies, only the intake result.
- The **Reply Agent is the fan-in point** and adapts its message to the scenario path.
- With an incomplete request, valuation is **skipped** and the reply asks for the
  missing fields — the agent never guesses a value.

## Scenario Paths

| Path | Condition | Stock | Valuation | Scheduling | Reply |
|------|-----------|-------|-----------|------------|-------|
| 1 | Complete + in stock | available | value range | 3 slots | positive confirmation |
| 2 | Complete + not in stock | not_found/sold + alternatives | value range | 3 slots | alternatives offered |
| 3 | Incomplete | runs if desired car is known | skipped | 3 slots | request missing fields |
| 4a | RDW lookup fails | — | fallback to email data | — | normal reply, notes fallback |
| 4b | No free slots | — | — | no_slots | dealership will contact |
| 4c | Not in valuation guide | — | not_found | — | on-site valuation during appointment |

## Data Contracts

### Intake output (`TradeInRequest`)

```json
{
  "sender_name": "Jan Dijkstra",
  "sender_email": "j.dijkstra@example.com",
  "desired_car": {
    "make": "Volkswagen",
    "model": "Golf",
    "trim": "1.5 TSI Comfortline",
    "found_in_email": true
  },
  "trade_in_car": {
    "license_plate": "X-123-AB",
    "make": "Opel",
    "model": "Astra",
    "year_of_manufacture": 2018,
    "mileage_km": 125000,
    "fuel_type": "petrol",
    "transmission_type": "manual"
  },
  "completeness": {
    "is_sufficient_for_valuation": true,
    "missing_fields": []
  }
}
```

### Completeness rule

Sufficient for valuation iff the trade-in car has at least:
**make + model + (year_of_manufacture OR license_plate) + mileage_km**.
`fuel_type` and `transmission_type` are optional — the RDW lookup can supply
them when a license plate is available.

### Fixed assumptions

| Assumption | Value |
|------------|-------|
| Appointment duration | 60 minutes |
| Business hours | Mon-Fri, 09:00-17:00 |
| Look-ahead window | 7 days |
| Slots proposed | 3 |
| Reference date (demo) | 2026-09-17 (fixed for reproducibility) |
| Calendar access | read-only — no slot allocation |

## Tools per Agent

| Agent | Tool | Data source | Production equivalent |
|-------|------|-------------|----------------------|
| Stock | `check_inventory`, `suggest_alternatives` | `data/inventory.json` | dealership DMS / website API |
| Valuation | `rdw_lookup` | `data/rdw_lookup.json` | RDW Open Data SODA API |
| Valuation | `lookup_value_range` | `data/valuation_guide.json` | licensed valuation feed (e.g. ANWB koerslijst) |
| Scheduling | `find_available_slots` | `data/calendar.json` | Microsoft Graph (read-only) |
