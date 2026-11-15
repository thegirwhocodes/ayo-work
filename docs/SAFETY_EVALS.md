# Safety evals and the rules they check

```bash
.venv/bin/python scripts/run_evals.py        # prints a PASS/FAIL table and "N/N scenarios passed"
.venv/bin/python -m pytest -q tests/test_eval_personas.py
```

The scenarios in `evals/personas.py` script a child (or a case-manager verdict) through the real call bridge with a
fake voice session. They use no network and no model, so they are deterministic: a pass means the rule held, not that
a model happened to behave. Each scenario was checked by deliberately breaking its rule and confirming it fails.

| Rule | Where it lives |
|---|---|
| No contact with a responder without a clear yes, in English, Pidgin, Yoruba or Hausa | `harness/consent.py` |
| A hesitant, conditional or off-topic reply is never consent | `harness/consent.py` |
| A short refusal withdraws consent and cancels pending responder jobs; a long sentence that only contains a negation does not | `harness/consent.py`, `TwilioMediaBridge._on_turn_complete` |
| An urgent call makes the voice agent check safety and offer help once, with one reminder if nobody has asked for help | `TwilioMediaBridge._on_guidance` |
| Guidance is pushed as context only (never forces a reply) and is marked as not from the caller | `TwilioMediaBridge._on_guidance` |
| The responder brief states risk, area, call-back number and communication tips first, from a closed vocabulary | `harness/incident_brief.py` |

## Consent matching

An answer counts as yes only if it is short, every word is a known yes or filler word, and at least one is a real yes
word. Any negation word makes it a no. Everything else is unclear and is treated as "not consented"; the voice agent
asks again. The word lists are fixed in `harness/consent.py`; they have not been reviewed by native speakers or a
safeguarding specialist, so have them reviewed before relying on them in real calls.

## Limits

- These scenarios test the application's rules, not what the voice model says. Whether the model follows the
  application's instructions on a real call needs live testing.
- A live, model-in-the-loop run of the same personas (scored by the case manager's safety rubric) is not included.
