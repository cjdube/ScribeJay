# Never make the model copy an opaque identifier

A small local model cannot reliably transcribe a random string. Ask it to echo
a 26-character Google event id and it will spend its token budget copying
characters, second-guess itself, and either return nothing at all or return the
id subtly wrong. Both outcomes are silent.

## The rule

**Number the items `1..N` for the model. Keep the number → id mapping in
Python.**

```python
# The prompt sees this
[{"n": 1, "summary": "Standup"}, {"n": 2, "summary": "1:1 with Dana"}]

# Python maps the answer back
scores[batch[n - 1]["id"]] = (score, angle)
```

Small integers are cheap to emit and trivial to validate. A number outside
`1..len(batch)` is obviously bad and gets dropped; a mis-copied UUID looks
exactly like a real one and matches nothing.

## Two failure shapes

### The calendar colorizer — total failure

`scribejay/calendar_colorizer.py`, 2026-07-25. Google's 26-character event ids went
out in the prompt and came back as the response's keys.

The model spent all 3,072 `num_predict` tokens **inside its thinking block**,
transcribing one id character by character and second-guessing itself. It
finished with `done_reason: "length"` and emitted no content at all. The parser
could only report it as bad JSON, which sent the investigation toward the
prompt format rather than the budget.

Even the runs that *succeeded* mis-copied the id — dropping two characters — so
the event silently matched nothing and was never colored.

Fixed by `_classify_input`, which numbers the events and shows the model nothing
but `{"n": n, "summary": ...}`.

### The worse version: a parser that degrades

The colorizer crashed, which is the lucky outcome. The same mistake in a task
whose parser degrades to an empty result is silent: the run succeeds, the page
is written, and the items that were meant to be scored simply are not.

A batch scoring 8 of 10 items has been observed losing the other two with no
trace at all. That is why the bounds-check below is paired with a count-gap
WARNING — see [model-constraints.md](model-constraints.md).

## Why this is worse than a normal bug

It has two failure modes and neither one raises:

| Mode | What you see |
|---|---|
| Total | An empty response. The parser reports "bad output", pointing you at the wrong cause. |
| Partial | A well-formed answer carrying an id that matches no record. The item is silently skipped. |

The partial mode is the dangerous one. The run succeeds, the log looks clean,
and the work simply didn't happen.

This also compounds with the thinking-budget problem: transcription happens
*inside* the thinking block, so it consumes the same `num_predict` the answer
needs. See [model-constraints.md](model-constraints.md).

## The pattern

Compact for the prompt — number the items and send only the fields the model
needs:

```python
def _compact_for_scoring(items: list) -> list:
    return [
        {"n": n, "signal": i["signal"], "company": i["company"], ...}
        for n, i in enumerate(items, 1)
    ]
```

Parse defensively — validate the number, then map back in Python:

```python
n = int(parts[0].strip())
if not 1 <= n <= len(batch):
    continue
scores[batch[n - 1]["id"]] = (score, angle)
```

Reference implementation: `scribejay/calendar_colorizer.py:_classify_input` /
`_apply_classification`.

## What counts as an opaque identifier

Anything the model has no semantic grip on:

- Google event, message, and calendar ids
- ClickUp task and list ids
- UUIDs, hashes, and content-addressed keys
- Long URLs used as keys

A short human-meaningful string is fine — the model can copy `"Standup"`,
because it understands it. The test is whether a one-character slip would be
detectable. With a number it is. With a UUID it is not.

## Checklist

1. Does the prompt contain any id the model must send back? If so, number it instead.
2. Does the parser bounds-check the number before indexing?
3. If the model skips items, does the caller log the count gap? A parse yielding
   fewer results than inputs is a WARNING, not a shrug — see
   [model-constraints.md](model-constraints.md).

## Related

- [model-constraints.md](model-constraints.md) — the rest of the small-model rules
- [configuration.md](configuration.md) — where `OLLAMA_NUM_PREDICT` is set
