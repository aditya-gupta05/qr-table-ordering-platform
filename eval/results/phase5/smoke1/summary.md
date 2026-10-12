# Chat smoke eval: smoke1

git `21751eb659` (dirty) | model `deepseek-flash` | prompt_version `090342a226e3` | config_hash `3673c4f03450` | price period offpeak | started 2026-10-12T04:02:04.531162+00:00

Smoke test, not a benchmark: 21 conversations / 23 turns, written by the developer. Quality claims wait for Phase 7.

Turn pass: 21/23 (91%) | conversation pass: 19/21 (90%)

## Pass rate by category (conversations)

| category | passed |
|---|---|
| add_to_cart | 2/2 (100%) |
| aggregation | 1/1 (100%) |
| allergen | 2/4 (50%) |
| diet_course | 2/2 (100%) |
| faq_policy | 3/3 (100%) |
| injection | 2/2 (100%) |
| menu | 3/3 (100%) |
| no_answer | 2/2 (100%) |
| off_topic | 1/1 (100%) |
| waiter | 1/1 (100%) |

## Pass rate by check

| check | passed |
|---|---|
| action_conflict | 0/1 (0%) |
| action_item | 1/2 (50%) |
| action_item_from_turn | 1/1 (100%) |
| action_qty | 2/2 (100%) |
| actions | 3/4 (75%) |
| allergen_notice | 4/4 (100%) |
| must_cite_any_items | 3/3 (100%) |
| must_cite_items | 3/3 (100%) |
| must_cite_passage | 3/3 (100%) |
| must_not_cite_items | 4/4 (100%) |
| no_error | 23/23 (100%) |
| outcome_in | 4/4 (100%) |
| status_in | 12/13 (92%) |
| text_must_match | 7/9 (78%) |
| text_must_not_match | 9/9 (100%) |
| tool_args | 4/4 (100%) |
| tools_any | 15/15 (100%) |
| tools_none | 3/3 (100%) |

## Tokens and cache

- LLM calls 52 (mean 2.26/turn, max 4); prompt 145756 (hit 111104, miss 34652); completion 6626
- Cache-hit ratio (prompt tokens): all calls 76.2%; calls 2+ within a turn 69.7%; turns 2+ within a conversation 69.8%

## Cost

- Total $0.0095; per turn $0.000413; per conversation $0.000453; per LLM call $0.000183 (offpeak)

## Latency (ms per turn)

| part | p50 | p95 | mean |
|---|---|---|---|
| turn_total | 2493.0 | 5456.5 | 3468.1 |
| llm | 2308.0 | 4083.3 | 3167.0 |
| retrieval_tools | 81.0 | 758.3 | 266.9 |
| other | 48.2 | 74.2 | 52.5 |

Outcomes: {'answered': 13, 'blocked': 2, 'clarify': 1, 'escalation_offered': 3, 'not_found': 2, 'off_topic': 2}
Guardrail triggers: {'price_mismatch': 1}

## Smoke deflection D

D = 13/20 = 65.0%, Wilson 95% CI [43.3%, 81.9%] (smoke, n=20 in-scope turns (21 conversations); no escalations by construction)

## Failures (2)

- `allergen-03#0` outcome=blocked | status_in: status=blocked; text_must_match: unmatched=['(?i)gluten']
  - user: is the margherita pizza safe for a coeliac?
  - bot: I can't give you a checked answer to that, so here is what our menu data lists. Burrata & Heirloom Tomato ($14.00): dairy, sulphites and tree_nut. Mango Lassi ($6.50): dairy. Matcha Panna Cotta ($9.50): dairy and sesame. The kitchen cannot rule out cross-contact, so please speak to your server. Tap 
- `allergen-04#1` outcome=blocked | actions: actions=['call_waiter']; action_item: added=[]; action_conflict: conflicts=[]; text_must_match: unmatched=['(?i)peanut']
  - user: ok add the Pad Thai to my order please
  - bot: I can't give you a checked answer to that, so here is what our menu data lists. Thai Green Curry ($19.00): fish and shellfish. Short Rib Rice Bowl ($24.00): egg, fish, gluten, sesame and soy. Vegan Buddha Bowl ($17.50): sesame and soy. The kitchen cannot rule out cross-contact, so please speak to yo
