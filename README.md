# claude-jev-funnel

A Claude Code plugin for bulk, calibrated decisions: judge a batch of items with
TypeSafe's Jev model, resolve the confident ends in code, and send only the
uncertain band to Claude or a human.

[![CI](https://github.com/Shakibuzzaman3104/claude-jev-funnel/actions/workflows/ci.yml/badge.svg)](https://github.com/Shakibuzzaman3104/claude-jev-funnel/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)

## What it is

Jev ("System One") is a small, fast model built for one job: given a **state**
(text or JSON) and a set of **typed questions**, it returns calibrated
probabilities — a yes/no (`noul`), a pick from fixed options (`choice`), or a
position on a rubric (`score`). It doesn't generate text or reason step by
step; it's closer to a programmable `if` for judgments plain code can't make.

This repo packages that as a Claude Code skill (`jev`) plus a zero-dependency
Python CLI (`skills/jev/scripts/jev.py`) built around one shape, the **Jev
funnel**:

![The Jev funnel: code collects items, Jev judges them all, confident answers are handled in code, and only the uncertain few go to Claude or a human](docs/assets/funnel.svg)

1. Code collects items (reviews, logs, diffs, tickets, warnings).
2. `jev.py` sends them all in bulk with typed yes/no, pick-one, or rubric
   questions.
3. Confident answers (`YES`/`NO`/`ACT`/`CONFIDENT`) are handled in code, with
   no further model call.
4. Only the uncertain band (`UNCERTAIN`/`REVIEW`/`ABSTAIN`/`UNSURE`) goes to
   Claude or a human.

## Why

**Speed and cost.** List price is about $0.042 per million input tokens, and
output is free ([models](https://docs.typesafe.ai/models)). All questions in
one request see the same state and run in parallel, so batching pays off: in
TypeSafe's
[Parallel questions cookbook](https://docs.typesafe.ai/cookbooks/parallel_questions),
13 questions in one request took 0.27 s and ran about 12x cheaper and 10x
faster than one call per question, with the same answers. `usage.cost` on
every response, plus `jev.py usage`, tell you the real number instead of an
estimate.

On a bounded judging task in one published A/B
([Greenberg](https://dev.to/bengreenberg/jev-vs-claude-who-wins-4mln)), Jev's
median answer took 378 ms against 3,554 ms for Claude Sonnet 5 at high
reasoning, at 100% vs 99% accuracy over 306 decisions.

![Same judging task, time to answer: Jev 378 ms median at 100% accuracy; Claude Sonnet 5 3.55 s median at 99%](docs/assets/latency-race.svg)

![Batched vs one at a time: 13 questions in one request took 0.27 s and $0.000497; one request per question took 2.71 s and $0.006090](docs/assets/batching.svg)

In this repo's own [live benchmark](benchmarks/README.md), `jev.py batch`
judged 500 synthetic items with two questions each in 13 requests: a packed
40-item request took about 2.3 s at p50, the whole run took 3.92 s, and it
cost $0.0061.

![Measured with this CLI: 500 items, 13 requests, 3.92 s wall time, $0.006094 total](docs/assets/throughput.svg)

The CLI also guards against three measured pitfalls of Jev requests; see
[Built-in checks](#built-in-checks).

## Requirements

- **Claude Code**, to use the skill inside a session (not required just to
  run the CLI).
- **Python 3.9+** — the CLI is standard library only, nothing to install.
- An **OpenRouter API key with prepaid credit** (default transport), or a
  **TypeSafe API key** (`--provider typesafe`).

## Install

### Option 1: Claude Code plugin (recommended)

```
/plugin marketplace add Shakibuzzaman3104/claude-jev-funnel
/plugin install jev-funnel@claude-jev-funnel
```

Or the CLI equivalents:

```bash
claude plugin marketplace add Shakibuzzaman3104/claude-jev-funnel
claude plugin install jev-funnel@claude-jev-funnel
```

As a plugin, the skill is invocable directly as `/jev-funnel:jev`, and Claude
also reaches for it on its own when a task matches (see
[Using it from Claude](#using-it-from-claude)).

### Option 2: Manual copy

```bash
git clone https://github.com/Shakibuzzaman3104/claude-jev-funnel
mkdir -p ~/.claude/skills
cp -R claude-jev-funnel/skills/jev ~/.claude/skills/
```

To update, run `rm -rf ~/.claude/skills/jev` first (and `git pull` in the
clone), then repeat the copy. With a manual install the skill is invocable as
`/jev`.

### The CLI on its own

`skills/jev/scripts/jev.py` has no dependency on Claude Code and works from a
plain clone:

```bash
python3 skills/jev/scripts/jev.py --help
```

## Setup

Run these from a clone of this repo:
`git clone https://github.com/Shakibuzzaman3104/claude-jev-funnel && cd claude-jev-funnel`.
Inside Claude Code the skill runs the script from the plugin for you.

```bash
export OPENROUTER_API_KEY=sk-or-v1-...   # put this in your shell profile to persist it
python3 skills/jev/scripts/jev.py doctor
```

Get a key at <https://openrouter.ai/settings/keys> (the account needs prepaid
credit), and set a credit limit on it. `doctor` makes one tiny call to
confirm the key, model and provider work before a large run. Every response
carries its own cost (`usage.cost`), and `jev.py usage` totals what you've
spent from the local ledger — see [Privacy & data](#privacy--data).

## Quick start

A 60-second walkthrough using the bundled `examples/review-triage` example
(synthetic app-review text — see [`examples/README.md`](examples/README.md)).
Run it from the root of the same clone.

```bash
# 1. Validate the template and see the size/cost estimate; sends nothing.
#    The first request body goes to stdout, the estimate to stderr.
python3 skills/jev/scripts/jev.py batch \
  --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json \
  --dry-run > /dev/null

# 2. Try the shape offline with deterministic fake answers; no key needed
python3 skills/jev/scripts/jev.py batch \
  --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json \
  --mock --out examples/review-triage/answers.jsonl

# 3. Run it for real (needs OPENROUTER_API_KEY)
python3 skills/jev/scripts/jev.py batch \
  --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json \
  --out examples/review-triage/answers.jsonl --label review-triage
```

A real run never reuses the `--mock` rows from step 2; it answers every item
again. Step 2 writes one row per item (rows trimmed; `--mock` numbers are
deterministic fakes):

```jsonl
{"id": "r01", "answers": {"topic": {"type": "choice", "choice": "bug", "confidence": 0.69, "probabilities": {"bug": 0.744, "billing": 0.169, "performance": 0.075, "feature_request": 0.009, "praise": 0.0, "none": 0.002}}, "needs_reply": {"type": "noul", "noul": 0.47}, "severity": {"type": "score", "score": 2.23, "confidence": 0.22, "legend": {...}, "probabilities": {...}}}, "model": "mock", "cached": false}
{"id": "r02", "answers": {"topic": {"type": "choice", "choice": "performance", "confidence": 0.17, "probabilities": {"bug": 0.003, "billing": 0.309, "performance": 0.312, "feature_request": 0.014, "praise": 0.291, "none": 0.072}}, "needs_reply": {"type": "noul", "noul": 0.96}, "severity": {"type": "score", "score": 2.27, "confidence": 0.46, "legend": {...}, "probabilities": {...}}}, "model": "mock", "cached": false}
```

...and prints the band summary to stderr once the run finishes:

```
# items 20  done 20  cached 0  resumed 0  errors 0
# requests 1  input_tokens 7910  cost $0 (mock; ~$0.000332 at list price)
# latency p50 1 ms  p95 1 ms
# topic (choice): ACT 0  REVIEW 6  ABSTAIN 14
# needs_reply (noul): YES 5  NO 4  UNCERTAIN 11
# severity (score): CONFIDENT 3  REVIEW 5  UNSURE 12
# score answers are the least calibrated type (...); use them for ranking or thresholds and calibrate with `jev.py eval` before auto-acting on them
```

Act on it in code. Rows hold raw probabilities, so compute the band from each
answer (see [Reading answers](#reading-answers));
[`examples/README.md`](examples/README.md#reading-the-output) has a 15-line
example. Only the `REVIEW`/`UNCERTAIN`/`ABSTAIN`/`UNSURE` band needs a second
look, from you or a Claude subagent.

## Using it from Claude

Inside a Claude Code session, the skill triggers when a task matches its
description — bulk classifying, scoring, ranking, flagging or triaging a
batch of text — even if you don't say "Jev" by name. Installed as a plugin,
you can also call it directly with `/jev-funnel:jev`; a manual install uses
`/jev`.

To make the routing decision explicit rather than relying on triggering,
paste this into your project's `CLAUDE.md`:

```markdown
## Bulk judgments
Closed-set bulk judgments (yes/no, pick one, rubric) over ~20+ items →
the jev skill before any cheap LLM subagent; review only the uncertain band.
```

## Commands

| Command | Purpose |
| --- | --- |
| *(none)* | `ask` — one request: `--request FILE`, or `--state` plus `--noul`/`--choice`/`--score` |
| `batch` | Judge many items with a question template (JSONL in, JSONL out, resumable) |
| `rank` | Sort items by one yes/no question |
| `eval` | Accuracy and calibration of a template on labeled cases |
| `doctor` | Check provider, key, credit and model with one tiny call |
| `usage` | Totals from the local usage ledger |

Full flag-by-flag reference, input/output formats, caching, resume rules and
exit codes: [`docs/cli.md`](docs/cli.md).

## Reading answers

Each question type calibrates differently, so band on the field that type
actually calibrates — not one confidence cutoff for all three:

| Type | Band on | Bands | Notes |
| --- | --- | --- | --- |
| `noul` | `noul` (P(yes)) | `YES` ≥0.8 · `NO` ≤0.2 · else `UNCERTAIN` | Best-calibrated type (ECE 0.012); no `confidence` field |
| `choice` | `p_max` (top probability) | `ACT` ≥0.8 · `REVIEW` 0.5–0.8 · `ABSTAIN` <0.5 | ECE 0.086; `confidence` is a fixed function of `p_max` and option count, so the same cutoff means a different `p_max` for 3 options than for 15 |
| `score` | `confidence` | `CONFIDENT` ≥0.8 · `REVIEW` 0.5–0.8 · `UNSURE` <0.5 | Least-calibrated type (ECE 0.254); treat as a ranking/threshold signal, not something to act on directly |

![How far to trust each answer type: expected calibration error is 0.012 for noul, 0.086 for choice and 0.254 for score](docs/assets/calibration.svg)

ECE figures are from an independent pre-registered test
([PrimeLine](https://primeline.cc/blog/typesafe-jev-pre-registered-test)).
These bands are starting points, not rules for your data. **Calibrate on your
own labels**: `jev.py eval --cases labeled.jsonl --template q.json` prints
per-question accuracy, a per-bucket calibration table, and the loosest
threshold that reaches a target precision — see
[`eval` in `docs/cli.md`](docs/cli.md#eval).

## When not to use it

- A skill router built on top of Jev inside Claude Code. Full skill
  descriptions are already shown, so a router on top adds little.
- Screening code files for relevance before reading them, on an
  already-manageable codebase. A 390k-line-repo benchmark found no effect
  beyond run-to-run noise ([jev-axi](https://github.com/CHLIN0/jev-axi)); it's
  worth it on much larger candidate sets.
- Repeat-and-vote in production. `noul` answers already hold still
  (std-dev ≈0.01 over repeats,
  [consistency_noul_cookbook](https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook))
  and there's no server-side cache, so voting just pays twice.
- A single judgment you can make yourself, or anything that's really
  generation, counting, dates, or arithmetic.

## Built-in checks

Before anything is sent, `jev.py` lints the request and refuses to proceed on
an error (or a detected secret, which `--lenient` never relaxes):

- **Structural validation** — question type, instructions, and criteria shape.
- **A `choice` needs a no-match option** (`none`/`other`/...). When the true
  answer is missing from the option set, Jev picks a wrong one at confidence
  1.00 ([wellposed](https://github.com/suraj-phanindra/wellposed)). `jev.py`
  rejects a `choice` with no no-match option unless you pass
  `--allow-no-none`, which downgrades it to a warning.
- **Descriptive score levels.** Independently measured expected calibration
  error is `0.012` for `noul`, `0.086` for `choice`, and `0.254` for `score`
  ([PrimeLine](https://primeline.cc/blog/typesafe-jev-pre-registered-test)).
  The CLI bands each type on the field that calibrates (see
  [Reading answers](#reading-answers)) and warns when a score level is a bare
  number or a one/two-word label, which the
  [official Score docs](https://docs.typesafe.ai/primitives/score) show
  calibrates far worse than a descriptive one.
- **Positional references.** Pointing at `items[7]` instead of a stable id
  lets an answer land on the wrong item with no error: 29 wrong out of 320
  items at 25 items per request, 0 out of 320 with keyed ids
  ([gist](https://gist.github.com/pedramamini/014676fa8684d91bf7000f4623701ada)).
  `batch`/`rank`/`eval` place each item for you (keyed ids, or inside its own
  question for `rank`), and the CLI warns on positional references anywhere
  else.

  ![Referencing items by position vs by key: 29 of 320 answers landed on the wrong item by position, none by key](docs/assets/keyed-vs-positional.svg)

- **Secret scanning** — every request (state, questions, criteria) is scanned
  for common key/token patterns (OpenRouter, Anthropic, AWS, GitHub, Google,
  Slack, private-key blocks) and a match blocks the send.
- **The 32k context limit** (state + all questions combined, on OpenRouter)
  is enforced before sending; an oversized item is reported as an error row
  with a hint to trim it or use `--fields`.

## Building Jev into apps

Calling Jev from an app or service is a different job from judging things
inside a Claude Code session — see `skills/jev/SKILL.md`'s "Mode B" and:

- [`skills/jev/references/api.md`](skills/jev/references/api.md) — request/response shapes, limits, errors, plain HTTP examples
- [`skills/jev/references/kotlin.md`](skills/jev/references/kotlin.md) — Android app + JVM backend
- [`skills/jev/references/swift.md`](skills/jev/references/swift.md) — iOS app + backend

**Never ship an API key in client code** — a mobile app, an APK/AAB, or a
browser bundle. Anyone can extract it, and an OpenRouter key is especially
costly to leak since it can call every model on the account. Call Jev from
your own backend, or, for content you already bundle, tag it once at build
time with a batch job and ship the tags instead of a live key.

## Privacy & data

**Leaves the machine:** the `state` and `questions` of every call, sent to
OpenRouter (default) or TypeSafe (`--provider typesafe`), plus the API key in
the `Authorization` header. TypeSafe doesn't train on requests; OpenRouter's
own data policy also applies on that route.

**Stays local:**

- **Usage ledger** — one JSON line per HTTP attempt, at
  `~/.config/jev/usage.jsonl` (override with `JEV_LEDGER`, or turn it off
  with `JEV_NO_LEDGER=1`).
- **Answer cache** — a SQLite file at `~/.cache/jev/cache.sqlite` (override
  with `JEV_CACHE_DIR`), keyed by model + pack mode + template + item payload.
- **Secret scanning** happens before a request ever leaves the machine (see
  [Built-in checks](#built-in-checks)) — never disabled by `--lenient`.

## Project layout

```
.claude-plugin/
  plugin.json              plugin manifest
  marketplace.json         marketplace manifest
skills/jev/
  SKILL.md                 the skill
  scripts/jev.py           the CLI
  references/
    api.md                 API contract, limits, errors
    question-design.md     structuring state, patterns, confidence
    recipes.md             ready-made request shapes
    kotlin.md              Android/JVM integration
    swift.md               iOS integration
examples/
  review-triage/           batch example
  rank-candidates/         rank example
  eval/                    eval example
benchmarks/                throughput benchmark (live, OpenRouter)
tests/
  test_jev.py              offline unit tests
docs/
  cli.md                   full CLI reference
  assets/                  README diagrams (SVG)
```

## Development & testing

No dependencies to install beyond Python 3.9+. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow, running the tests
(`python3 -m unittest discover -s tests`), running the examples with
`--mock`, style rules, and the release process.

## Sources & further reading

- TypeSafe docs: <https://docs.typesafe.ai/llms.txt> (append `.md` to any
  page path for Markdown) and the cookbooks under `/cookbooks/`
- OpenRouter's Jev model page and tutorial:
  <https://openrouter.ai/blog/tutorials/jev-vs-llm-as-a-judge/>
- Calibration measurement: <https://primeline.cc/blog/typesafe-jev-pre-registered-test>
- Missing no-match option: <https://github.com/suraj-phanindra/wellposed>
- Positional references: <https://gist.github.com/pedramamini/014676fa8684d91bf7000f4623701ada>
- Prompt injection moving a verdict: <https://venturebeat.com/security/companies-are-putting-jev-in-charge-of-ai-agent-decisions-and-prompt-injection-can-influence-the-verdict>

More links, including per-recipe sources, are in
[`skills/jev/references/recipes.md`](skills/jev/references/recipes.md) and
[`skills/jev/references/question-design.md`](skills/jev/references/question-design.md).

## Disclaimer

This is an independent, community project. It is not affiliated with,
endorsed by, or sponsored by TypeSafe AI, OpenRouter, or Anthropic. "Jev",
"System One", "OpenRouter", "Claude", and "Claude Code" are trademarks of
their respective owners.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
