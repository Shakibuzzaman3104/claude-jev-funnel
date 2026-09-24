# Benchmarks

A small, reproducible **throughput** benchmark for `jev.py batch`: how many
requests a batch run takes, how long each request takes, how many input
tokens it uses, and what it costs — measured live against the OpenRouter
route, not estimated.

## What this measures

- Wall-clock time for a batch run of N items, two questions each, with
  `--workers 8`.
- Per-request latency: p50 / p95 from the run's own summary (nearest-rank,
  timed around the whole call), min / max from `$JEV_LEDGER` (timed per HTTP
  attempt). The two timings agree to within a few ms: the ledger's p50 is
  2274 ms against the summary's 2279 ms.
- Total input tokens and total cost, as **reported by OpenRouter** for that
  run (not the `--dry-run` list-price estimate).
- Error count and the resulting decision-band counts (`ACT/REVIEW/ABSTAIN`
  for the `choice` question, `YES/NO/UNCERTAIN` for the `noul` question).

## What this does NOT measure

- **No accuracy or calibration claim.** Nothing here checks whether Jev's
  answers were *correct* — there are no ground-truth labels. For accuracy
  and calibration, see `jev.py eval` and `examples/eval/`.
- **Not a controlled, averaged benchmark.** This is one run, on one machine,
  over one network path, at one moment in time. Latency and wall time will
  vary with your connection, OpenRouter's load, and the machine running the
  CLI. Treat the numbers as indicative, not an SLA.
- **Synthetic data.** The items are template-generated app-review-style text
  (`make_items.py`), not real user reviews, and reference no real product.
- **Not a cross-model or cross-provider comparison.** This exercises the
  `openrouter` route only.

## Files

- `make_items.py` — deterministic generator of short, synthetic,
  app-review-style items (bug reports, feature requests, praise, and vague
  ones). Same seed, same count, same output every time.
- `template.json` — the two-question template used for the run: a `choice`
  question (`topic`: bug / feature / praise / other / none) and a `noul`
  question (`blocking`: does the text report something that stops the app
  from working).
- `results/2026-09-24.json` — the summary numbers from the run described
  below (no raw answers, no key, no machine-specific paths).

## Reproduce it

Run from the repository root, with your own `OPENROUTER_API_KEY` already
exported in your shell (never pass it on the command line):

```bash
# 1. Generate the same 500 deterministic items used for the 2026-09-24 run.
python3 benchmarks/make_items.py 500 > /tmp/jev-bench-items.jsonl

# 2. Optional: lint the template and see the token/cost estimate first — sends nothing.
python3 skills/jev/scripts/jev.py batch \
  --items /tmp/jev-bench-items.jsonl \
  --template benchmarks/template.json --dry-run

# 3. Run it live. Requests are small (~40 items packed per request by
#    default): well under $0.01 total at 500 items.
mkdir -p /tmp/jev-bench
JEV_LEDGER=/tmp/jev-bench/ledger.jsonl JEV_CACHE_DIR=/tmp/jev-bench/cache \
  /usr/bin/time -p python3 skills/jev/scripts/jev.py batch \
  --items /tmp/jev-bench-items.jsonl \
  --template benchmarks/template.json \
  --out /tmp/jev-bench/answers.jsonl \
  --no-cache --workers 8 --label benchmark
```

The batch command prints a summary to stderr (items, requests, input
tokens, cost, p50/p95 latency, band counts). `/usr/bin/time -p` reports wall
time (`real`). Per-request detail (one row per HTTP attempt, with
`latency_ms`, `input_tokens`, and `cost_usd`) is in `$JEV_LEDGER`.

Change `--workers` or the item count to see how throughput scales; a larger
run costs proportionally more (`--dry-run` always tells you how much before
you send anything).

## Results: 2026-09-24

Live run against `openrouter` / `typesafe/jev-1.13-20260917`, 500 synthetic
items, 2 questions each, `--workers 8`, `--no-cache`. Full machine-readable
summary in [`results/2026-09-24.json`](results/2026-09-24.json).

| Metric | Value |
| --- | --- |
| Items | 500 |
| Questions per item | 2 |
| Requests | 13 |
| Workers | 8 |
| Wall time (real) | 3.92 s |
| Latency p50 | 2279 ms |
| Latency p95 | 2355 ms |
| Latency min / max (ledger) | 511 ms / 2355 ms |
| Input tokens | 145,106 |
| Cost (reported) | $0.006094 |
| Errors | 0 |
| `topic` bands | ACT 481, REVIEW 19, ABSTAIN 0 |
| `blocking` bands | YES 88, NO 381, UNCERTAIN 31 |

## Machine and network caveat

This run was made from one developer machine over one residential/ISP
network path to OpenRouter, at one point in time. Latency includes that
network hop and OpenRouter's routing to the model provider, both of which
vary. Re-running `make_items.py` with the same count
reproduces the exact same input items (seeded `random.Random(2026)`), but
re-running the live call will not reproduce the exact same latency, cost, or
even necessarily the exact same answers, since Jev's output is probabilistic.
