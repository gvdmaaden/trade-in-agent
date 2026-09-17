# Observability Strategy

## What we monitor

| Metric | Source | Alert threshold | Why |
|--------|--------|-----------------|-----|
| Agent response latency | Application Insights | p95 > 10 s | customer expects fast reply turnaround |
| Tool call success rate | Application Insights | < 99% | a failing tool breaks grounding |
| Tool call latency | Application Insights | p95 > 3 s | slow lookups slow the whole fan-out |
| Intake parse failures | custom event | > 2% | structured output is the workflow's spine |
| Valuation groundedness violations | evaluation runs | any occurrence | values must come from the tool, never the model |
| Path distribution (1/2/3/4) | custom metrics | drift > 20% vs baseline | signals input mix changes or parser regressions |
| Reply approval rate (human-in-the-loop) | CRM / manual | < 80% approved unedited | proxy for reply quality |

## Tracing

Microsoft Foundry emits GenAI traces to Application Insights. Key trace points:

1. **Intake agent invocation** — input email, parsed JSON, completeness decision
2. **Fan-out spans** — one span per specialist agent (stock / valuation / scheduling),
   running in parallel
3. **Tool calls** — function name, arguments, result, latency
4. **Fan-in** — reply agent input (all partial results) and final draft
5. **Gate decisions** — especially "valuation skipped: incomplete request"

## How traces answer operational questions

- *"Why did this customer get a value of €0?"* → trace shows whether
  `lookup_value_range` returned not_found, or whether the intake gate
  incorrectly passed an incomplete request.
- *"Why is this email slow?"* → span waterfall shows which parallel branch
  dominated the latency.
- *"Did the model invent a price?"* → compare reply text against the
  valuation tool result in the same trace; groundedness violations are
  detectable, not just plausible.

## Improvement loop

1. Weekly review of failed/edited replies (human-in-the-loop feedback)
2. Add recurring failure patterns to `evaluation/eval_cases.json`
3. Re-run the evaluation before and after every prompt or model change
4. Gate deployments on evaluation score (see production_readiness.md)
