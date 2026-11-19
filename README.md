# Nordavo Roosterly helpdesk agent

Deterministic FAQ helpdesk agent for the Nordavo take-home. No LLM calls (€0). Answers only from `faq.md`.

## How to run

```bash
cp .env.example .env
# Set HELPDESK_API_KEY from the brief.
# Before the scored run: set HELPDESK_CANDIDATE_EMAIL to the email you applied with
# (the example value is a dummy practice address — a different email is a different account).
python test_agent.py          # offline unit tests
python run_eval.py            # score decide() vs GET /examples
python run_agent.py           # one pass over open tickets
```

Python 3.11+. Stdlib only — nothing to `pip install`.

## Decisions

- Rule-based routing: safety patterns (security/legal/AVG/refund/etc.) always hand off; else weighted FAQ phrase match; else handoff when unsure.
- Reply text is canned NL/DE frames or the English FAQ body — never ticket text, never invented facts.
- Language heuristic over NL/EN/DE word lists; empty and spam tickets close with no reply.

## Reliability

- Local state skips already-handled ticket IDs so a re-run resumes safely.
- HTTP client retries 5xx with backoff, respects `Retry-After` on 429; 409 marks done; 422 is logged and not marked done.
- Logs redact IBAN/card-like strings.

## Eval and fix

`run_eval.py` compares `decide()` to gold labels from `/examples` (language, category, priority, action). Failures print gold vs pred so FAQ weights or safety phrases can be tightened offline before another live pass.

## What I would improve

- Richer language detection and more FAQ phrases from real misspellings.
- Structured confidence calibration against a held-out example set.
- Clearer separation of “unsure FAQ” vs “no match” for handoff notes.
- Optional dry-run mode that prints decisions without POSTing.
