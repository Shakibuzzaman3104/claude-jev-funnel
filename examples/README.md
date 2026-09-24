# Examples

Three small, runnable examples for `jev.py`. All sample data is synthetic: the app
reviews, search snippets, and CI logs are invented for these examples.

| Example | Command | What it shows |
| --- | --- | --- |
| [`review-triage/`](review-triage/) | `batch` | Three question types over 20 app-store-style reviews: a `choice` with a no-match option, a `noul` with explicit criteria, and a `score` with descriptive levels. |
| [`rank-candidates/`](rank-candidates/) | `rank` | Sorting 20 search-result snippets by one yes/no question, from clearly relevant to off-topic. |
| [`eval/`](eval/) | `eval` | Measuring accuracy and calibration on 16 labeled CI failures before you trust a threshold. |

Run every command from the repository root. Each example runs in three modes:

- `--dry-run` lints the questions, packs the items into requests, prints the first request
  body on stdout and a size and cost estimate on stderr. It sends nothing and needs no key.
- `--mock` runs the whole pipeline offline with deterministic fake answers. Use it to check
  plumbing and output shape. The numbers mean nothing.
- No flag makes a real call. It needs `OPENROUTER_API_KEY` (or `TYPESAFE_API_KEY` with
  `--provider typesafe`). Run `python3 skills/jev/scripts/jev.py doctor` once first.

Real runs are cheap. By the dry-run estimate, each example is one request of about 2,000
to 8,000 input tokens: a small fraction of a cent at TypeSafe's list price of $0.042 per
million input tokens ([docs.typesafe.ai/models](https://docs.typesafe.ai/models)). Every
live response reports its actual cost in `usage.cost`.

## review-triage

Triage app reviews: what each one is about, whether support should reply, and how badly
the problem affects the reviewer.

```bash
python3 skills/jev/scripts/jev.py batch \
  --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json --dry-run

python3 skills/jev/scripts/jev.py batch \
  --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json --mock

python3 skills/jev/scripts/jev.py batch \
  --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json \
  --out examples/review-triage/answers.jsonl --label review-triage
```

[`template.json`](review-triage/template.json) follows the question-design rules the
linter enforces:

- `topic` (`choice`) includes `"none": "None of the listed subjects fits the review"`.
  Without a no-match option, Jev has to pick a listed option even when none fits.
- `needs_reply` (`noul`) asks one narrow yes/no question and spells out what counts as
  true and false in `criteria`.
- `severity` (`score`) uses short descriptive levels, never bare numbers.
- Every question points at the item by name (`item.text`), never by position.

## rank-candidates

Rank search results by whether they answer one specific question.

```bash
Q="Does this search result give steps to fix a pip install that fails with an SSL certificate verification error?"

python3 skills/jev/scripts/jev.py rank \
  --items examples/rank-candidates/items.jsonl --question "$Q" --dry-run

python3 skills/jev/scripts/jev.py rank \
  --items examples/rank-candidates/items.jsonl --question "$Q" --mock --top 10

python3 skills/jev/scripts/jev.py rank \
  --items examples/rank-candidates/items.jsonl --question "$Q" --top 10 \
  --out examples/rank-candidates/answers.jsonl
```

The snippets have a deliberate spread: direct fixes for the pip error (proxy CA
certificates, `pip config set global.cert`, `PIP_CERT`, the macOS certificate installer),
near misses (SSL errors in `requests` or `git`, not pip), and unrelated pages (nginx
certificates, Python IDEs). A real run should put the direct fixes at the top and the
unrelated pages at the bottom. `rank` prints a table of `rank`, `p` (the probability the
answer is yes), `band`, `id`, and a preview. `--out` writes the ranked rows as
`{"id", "p", "band"}`, best first. `--min` applies to this file too; items that errored
are left out (they are listed after the table). Add `--min 0.8` to keep only the `YES`
band.

## eval

Check how well a template does on cases you have already labeled, and which threshold
reaches your target accuracy.

```bash
python3 skills/jev/scripts/jev.py eval \
  --cases examples/eval/cases.jsonl \
  --template examples/eval/template.json --fields text --dry-run

python3 skills/jev/scripts/jev.py eval \
  --cases examples/eval/cases.jsonl \
  --template examples/eval/template.json --fields text --mock

python3 skills/jev/scripts/jev.py eval \
  --cases examples/eval/cases.jsonl \
  --template examples/eval/template.json --fields text
```

Each case carries its label in `expect`, which is never sent:

```json
{"id": "c08", "text": "FAILED test_cache.py::test_concurrent_writes ...", "expect": {"flaky": true, "cause": "test_failure"}}
```

A `noul` label is `true` or `false`, a `choice` label is an option key, and a `score`
label is a level index or the exact level text. `--fields text` sends only the `text`
field, so no other field can leak the answer.

The report shows, per question, overall accuracy, a table of accuracy by probability
bucket, band counts, and the loosest threshold that reaches `--target` (default 0.95).
Under `--mock` the answers are random, so expect low accuracy and the warning
`confidence is not tracking accuracy`. Sixteen cases show the report format. Label more
cases from your own data before you trust a threshold.

## Reading the output

`batch` writes one JSON row per item, to stdout or to `--out`:

```json
{"id": "r03", "answers": {"topic": {...}, "needs_reply": {...}, "severity": {...}}, "model": "typesafe/jev-1.13", "cached": false}
{"id": "r07", "error": "..."}
```

The summary on stderr counts each question's answers by band. Each type calibrates
differently, so each gets its own band field:

| Type | Band on | Bands |
| --- | --- | --- |
| `noul` | `noul`, the probability of yes | `YES` at 0.8 or above, `NO` at 0.2 or below, `UNCERTAIN` in between |
| `choice` | the top probability in `probabilities` | `ACT` at 0.8 or above, `REVIEW` from 0.5 to 0.8, `ABSTAIN` below 0.5 |
| `score` | `confidence` | `CONFIDENT` at 0.8 or above, `REVIEW` from 0.5 to 0.8, `UNSURE` below 0.5 |

This is the funnel: handle the confident bands in code and send only the uncertain band
to Claude or a person. For example, with the review-triage output:

```python
import json

with open("examples/review-triage/answers.jsonl", encoding="utf-8") as rows:
    for line in rows:
        row = json.loads(line)
        if "error" in row:
            print("retry", row["id"])
            continue
        p = row["answers"]["needs_reply"]["noul"]
        if p >= 0.8:
            print("support queue", row["id"])
        elif p <= 0.2:
            print("no reply needed", row["id"])
        else:
            print("needs review", row["id"])
```

These thresholds are starting points. Use `eval` on your own labeled data to set them,
and use higher bars for actions that are hard to undo.

A rerun with the same `--out` skips ids that already have answers and retries error
rows. If you reword a question, use a new `--out` file.
