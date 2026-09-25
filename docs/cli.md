# CLI reference

Complete reference for `skills/jev/scripts/jev.py` — a dependency-free Python
3.9+ CLI. Run `python3 skills/jev/scripts/jev.py --help` or
`python3 skills/jev/scripts/jev.py <command> --help` for the same information
from the tool itself; this document adds the input/output formats, resume and
caching rules, and provider limits that don't fit in `--help` text.

## Contents

- [Global behavior](#global-behavior)
- [`ask` (default, no subcommand)](#ask-default-no-subcommand)
- [`batch`](#batch)
- [`rank`](#rank)
- [`eval`](#eval)
- [`doctor`](#doctor)
- [`usage`](#usage)
- [Input formats](#input-formats)
- [Template format](#template-format)
- [Output row format](#output-row-format)
- [`--out`: resume and compaction](#--out-resume-and-compaction)
- [Answer cache](#answer-cache)
- [Usage ledger](#usage-ledger)
- [Bands](#bands)
- [Lint checks](#lint-checks)
- [Environment variables](#environment-variables)
- [Exit codes](#exit-codes)
- [Providers and limits](#providers-and-limits)

## Global behavior

`jev.py <command> ...` dispatches to one of `batch`, `rank`, `eval`, `doctor`
or `usage`. Anything else is treated as the `ask` (single-request) form.

Shared across `ask`, `batch`, `rank` and `eval`:

- `--provider {openrouter,typesafe}` — API route (default `openrouter`; env
  `JEV_PROVIDER`).
- `--model MODEL` — model id (default `$JEV_MODEL`, else
  `typesafe/jev-1.13` on OpenRouter or `jev-1.13.0` on TypeSafe direct).
- `--label LABEL` — tag stored with each call in the usage ledger.
- `--dry-run` — lint and print the request body with a token/cost estimate;
  sends nothing.
- `--mock` — deterministic fake answers, entirely offline: no key required,
  no ledger row written, no cache read or write.
- `--lenient` — turn lint *errors* into warnings. Malformed questions (bad
  types, wrong criteria shape) and detected secrets are never relaxed by this
  flag.
- `--allow-no-none` — allow a `choice` with no no-match option, downgrading
  that specific check from an error to a warning.

`batch`, `rank` and `eval` additionally share:

- `--pack {keyed,inline}` — `keyed`: items sit in one shared `state` under
  generated keys (`k00001`, `k00002`, ...); `inline`: each question carries
  its own item under an `item` key. Default is `keyed` for `batch`/`eval`,
  `inline` for `rank`.
- `--fields a,b,c` — send only these item fields (default: every field except
  `id`, and except `expect` for `eval`).
- `--per-request N` — max items packed into one request (default 40); a
  request also stays under 80% of the provider's token limits, so the actual
  count per request can be lower.
- `--workers N` — parallel in-flight requests (default 8).
- `--no-cache` — skip the local SQLite answer cache for this run.

A request is built, then **linted before anything is sent**: structural
checks always run; content and reference checks (see
[Lint checks](#lint-checks)) print as errors (or warnings under `--lenient`)
and a secret finding always blocks. `Ctrl-C` during a run exits `130`; for
`batch` with `--out`, rerunning the same command resumes.

`jev.py --version` prints the CLI version (top level only; subcommands don't
take it).

## `ask` (default, no subcommand)

```
jev.py [--request REQUEST | --state STATE --noul ID INSTRUCTIONS | --choice ... | --score ...]
       [--json] [--provider ...] [--model ...] [--label ...]
       [--dry-run] [--mock] [--lenient] [--allow-no-none]
```

One request, printed as formatted lines (or raw JSON with `--json`).

- `--request REQUEST` — the full request body: a path, `-` for stdin, or
  inline JSON (`{"state": ..., "model": ..., "questions": {...}}`).
- `--state STATE` — state as literal text/JSON, `@path`, or `-` for stdin.
  Used with `--noul`/`--choice`/`--score` instead of `--request`.
- `--noul ID INSTRUCTIONS` — add a yes/no question (repeatable).
- `--choice ID INSTRUCTIONS OPTION[=desc] OPTION[=desc] ...` — add a choice
  (repeatable); include a no-match option such as `none=No listed option fits`.
- `--score ID INSTRUCTIONS LEVEL LEVEL ...` — add a score, lowest level first,
  2–10 descriptive levels (repeatable).
- `--json` — print the raw response JSON, plus an injected `_latency_ms`
  field, instead of formatted lines.

Formatted output, one line per question:

```
sky                          noul    p(yes)=0.98  [YES]
department                   choice  billing  conf=0.81  p=0.88  margin=0.76  next=technical:0.12  [ACT]
frustration                  score   1.05 (~Frustrated but civil)  conf=0.92  [CONFIDENT]
```

A trailing `#`-prefixed line on stderr reports provider, model, input tokens,
cost (`cost=$x (reported)` from the API's `usage.cost`, or `cost~$x
(estimated)` from token count × list price when a response omits it), and
latency.

## `batch`

```
jev.py batch --items F --template F [--out F] [--pack {keyed,inline}]
             [--fields FIELDS] [--per-request N] [--workers N]
             [shared flags above]
```

Judge many items with one question template. `--items` and `--template` are
required.

- `--items F` — items: a JSON array or JSONL file, `-` for stdin.
- `--template F` — template JSON (see [Template format](#template-format)).
- `--out F` — append rows here as requests finish; a rerun resumes (see
  [`--out`: resume and compaction](#--out-resume-and-compaction)). Default is
  JSONL on stdout, which does not support resume.

Prints one `#`-prefixed summary block to stderr when the run finishes: item
counts (done/cached/resumed/errors), request/token/cost totals, p50/p95
latency, and a band breakdown per question (see [Bands](#bands)).

## `rank`

```
jev.py rank --items F --question TEXT [--true DESC] [--false DESC]
            [--context @F|JSON] [--top N] [--min P] [--out F]
            [shared/engine flags above]
```

Sorts items by the probability that one yes/no question holds — a single
`noul` run once per item.

- `--items F` — items, as for `batch`.
- `--question TEXT` — the yes/no question.
- `--true DESC` / `--false DESC` — optional `noul` criteria (what counts as
  yes / no).
- `--context @F|JSON` — shared data every request sees as `context`: literal
  JSON/text, `@path`, or `-` for stdin.
- `--top N` — rows to print (default 20). Answers are rounded to two
  decimals and often tie near 0.99; when the cut at `N` falls inside a tie,
  `rank` warns on stderr, because which tied items make the cut is arbitrary.
- `--min P` — keep only items with `p >= P`.
- `--out F` — write the ranked rows as JSONL, best first: `{"id", "p",
  "band"}`. `--min` applies to this file too; items that errored are left out
  (they are listed after the table). This is a plain sorted dump, not a
  resumable file in the `batch` sense.

With the default `--pack inline`, the question can say "this item" or
reference `item.text` directly, since each request only ever holds one item's
worth of context.

## `eval`

```
jev.py eval --cases F --template F [--target P] [--json]
            [shared/engine flags above]
```

Measures accuracy and calibration of a template against labeled cases.

- `--cases F` — labeled cases: same format as `batch` items, each with an
  `"expect": {"<qid>": value}` field (never sent to the model — always
  excluded from the payload). A case without a label for a given question is
  skipped for that question's metrics.
  - `noul`: `true`/`false` (also accepts `yes`/`no`, `1`/`0`).
  - `choice`: the expected option's key.
  - `score`: a 0-based level index, or the exact level text.
- `--template F` — template JSON, same format as `batch`.
- `--target P` — the precision/accuracy a suggested threshold must reach
  (default `0.95`).
- `--json` — print metrics as JSON instead of tables.

Use `--fields` deliberately here: with no `--fields`, every item field except
`id` and `expect` is sent, so make sure a stored verdict or prior score isn't
sitting in the item and leaking into the question.

Output (without `--json`): a header line (`cases`, `answered`, `errors`,
`target`), then per question:

- **noul**: accuracy at 0.5, a table by probability bucket (n, mean p, yes
  rate), band counts with precision for `YES`/`NO`, and the loosest threshold
  on each side that reaches `--target` (with its coverage), or "no threshold
  reaches the target".
- **choice**: overall accuracy, a table by `p_max` bucket (n, accuracy), band
  counts with accuracy, and the loosest `p_max` threshold that reaches
  `--target`.
- **score**: exact-match accuracy, "within one level" accuracy, mean absolute
  error, and a table by confidence bucket.

A warning prints per question when confidence isn't tracking accuracy in the
data (a signal to rewrite the question before trusting any threshold from it),
followed by the same batch summary block `batch` prints.

## `doctor`

```
jev.py doctor [--provider {openrouter,typesafe}] [--model MODEL] [--mock]
```

One minimal call to check a provider end to end. Prints provider, endpoint,
model, whether the relevant key env var is set, the ledger and cache paths,
account credit (OpenRouter only — TypeSafe direct has no balance endpoint),
and the result of one tiny `noul` call (HTTP status, latency, model, answer,
input tokens, cost). Prints `ok` and exits 0 only once every check passes.

## `usage`

```
jev.py usage [--since YYYY-MM-DD] [--label L] [--by {day,label,model}]
```

Totals from the local usage ledger (`$JEV_LEDGER` or
`~/.config/jev/usage.jsonl`), grouped by day (default), label, or model.
Prints a table of calls, errors, input tokens and cost per group, plus a
total row, and notes when part of the total cost is estimated rather than
API-reported. `--since` filters to calls on or after that UTC date (matched
against each row's `ts`); `--label` filters to calls made with that
`--label`. Exits 0 even when no ledger exists yet or no rows match; exits 2 on
a malformed `--since` or an unreadable ledger file.

## Input formats

**Items** (`batch`/`rank`/`eval` `--items`, and `eval`'s `--cases`): a `.json`
file containing a JSON array, or a JSONL file (one JSON value per line), or
`-` for stdin. Any input whose text starts with `[` is first parsed as a JSON
array. A JSONL line that isn't valid JSON becomes `{"text": "<the line>"}`.

- **id**: `item["id"]` if present (must be a string or integer, and unique
  across the file); otherwise the item's 0-based line/array index.
- **payload sent to Jev**: with `--fields a,b`, exactly those fields (an item
  missing one becomes an error row, listing the missing field(s)); without
  `--fields`, every field except `id` (and except `expect` for `eval` cases).
  An item with no fields left to send is also an error row.

## Template format

```json
{
  "context": {"...optional, shared by every request..."},
  "questions": {
    "is_crash": {
      "type": "noul",
      "instructions": "Does `item.text` report an app crash?"
    }
  }
}
```

Refer to the current item as `` `item` `` (e.g. `` `item.text` ``) and shared
data as `` `context` ``. Any template key other than `context`/`questions` is
ignored with a warning. Each question needs `type` (`noul`, `choice` or
`score`) and non-empty `instructions`; `choice` needs a `criteria` object with
2–255 options; `score` needs a `criteria` array with 2–10 ordered levels,
lowest first.

## Output row format

`batch` (stdout, or `--out`), one JSON object per line:

```json
{"id": "L001", "answers": {"category": {"type": "choice", "choice": "network", "confidence": 0.91, "probabilities": {"...": "..."}}}, "model": "typesafe/jev-1.13", "cached": false}
{"id": "L002", "error": "no answer returned for category"}
```

An answer that doesn't fit the question it was sent for (wrong type, a `choice`
outside the offered options, probabilities that don't sum to 1 or don't put the
choice on top, a `score` out of range) becomes an error row too — for example
`"invalid answer for category (choice 'x' is not one of the offered options)"` —
and is never cached.

`cached: true` marks an answer served from the local answer cache rather than
a live call. `rank --out` writes a different, simpler row per item:
`{"id": ..., "p": ..., "band": ...}`, sorted best (highest `p`) first.

## `--out`: resume and compaction

Passing `--out FILE` to `batch` makes the run resumable (`rank --out` is a
plain sorted dump and `eval` has no `--out`; their reruns reuse the local
answer cache instead):

- Rows are **appended as each request finishes**, not buffered to the end, so
  a killed run still leaves partial progress on disk.
- **On a rerun with the same `--out`**, an existing row for an id is reused
  only if: it has `"answers"` covering **every question id in the current
  template**, and — for a real (non-`--mock`) run — its `"model"` isn't
  `"mock"`. This check is purely structural: it matches on **question ids
  that were already answered, not on question wording**. If you reword a
  question's `instructions` but keep its id, a rerun will treat old rows for
  that id as already answered and won't re-send them. **Use a new `--out`
  file whenever you reword a question.**
- **At the end of the run**, the file is compacted to exactly one row per id,
  atomically (written to a temp file, then renamed over the original): rows
  for ids in the current `--items` file, in that file's order, followed by
  any leftover ids that existed in the old `--out` but aren't in the current
  input, in their original relative order.
- Interrupting with `Ctrl-C` prints a reminder to rerun with the same `--out`
  to resume; already-written rows are not lost.

## Answer cache

A local SQLite database (default `~/.cache/jev/cache.sqlite`, override with
`JEV_CACHE_DIR`) that survives across runs and across different `--out`
files, independent of the resume logic above:

- **Key**: SHA-256 of the canonical JSON `[model, pack, template, item
  payload]` — changing the model, `--pack` mode, the template (any question),
  or the item's own payload invalidates that item's cache entry.
- Checked before a request is sent, and written to after a live response
  comes back.
- Skipped entirely (no read, no write) when `--no-cache` or `--mock` is set.
- Opened lazily; a cache directory that can't be created or opened logs a
  warning and the run continues without caching.

## Usage ledger

A local, append-only JSONL file (default `~/.config/jev/usage.jsonl`,
override with `JEV_LEDGER`; disable entirely with `JEV_NO_LEDGER=1`). One row
is written **per HTTP attempt** (so a call retried twice writes up to three
rows: the failed attempts and the one that succeeds), with fields:

| Field | Meaning |
| --- | --- |
| `ts` | UTC timestamp, ISO-8601 |
| `cmd` | `ask`, `batch`, `rank`, `eval`, or `doctor` |
| `provider`, `model` | the route and model used |
| `label` | the `--label` value, or `null` |
| `questions` | number of questions in that request |
| `items` | number of items packed into that request (`null` for `ask`/`doctor`) |
| `input_tokens` | from the response's `usage`, or 0 for a failed attempt |
| `cost_usd` | reported cost, or a list-price estimate when the response omits `usage.cost` |
| `cost_source` | `"reported"` or `"estimated"` |
| `latency_ms` | wall time for that single attempt |
| `status` | `"ok"`, or `"http_<code>"` / `"network"` for a failed attempt |

`jev.py usage` reads this file to produce totals; nothing here is ever sent
anywhere.

## Bands

Default decision bands (starting points — calibrate with `eval` before
relying on them for anything that matters):

| Type | Band on | Bands |
| --- | --- | --- |
| `noul` | `noul` (P(yes)) | `YES` ≥ 0.8 · `NO` ≤ 0.2 · else `UNCERTAIN` |
| `choice` | `p_max` (chosen option's probability) | `ACT` ≥ 0.8 · `REVIEW` 0.5–0.8 · else `ABSTAIN` |
| `score` | `confidence` | `CONFIDENT` ≥ 0.8 · `REVIEW` 0.5–0.8 · else `UNSURE` |

## Lint checks

Findings are one of three severities. **Errors** block the send and exit `2`
unless `--lenient` downgrades them to warnings; **secrets** always block the
send, regardless of `--lenient`; **warnings** never block anything.

| Check | Default severity | `--lenient` | Notes |
| --- | --- | --- | --- |
| Malformed question (bad `type`, missing `instructions`, wrong `criteria` shape/size) | error (raises immediately) | still blocks | Not a lint finding — a structural `InputError` |
| `choice` with no no-match option | error | warning | `--allow-no-none` also downgrades this specifically |
| Backticked path doesn't resolve against state/instructions | error (warning if it's an `item.*` path checked only against the first item) | warning | |
| Positional reference (`items[7]`, or any path into a list of more than ~5 items) | warning | warning | Never an error; still worth fixing |
| Score level is a bare number or ≤2 words | warning | warning | |
| A `noul` phrased like a rating ("How ...", "Rate ...", "On a scale...") | warning | warning | |
| State exceeds ~8,000 tokens | warning | warning | Accuracy drops with irrelevant detail; not a hard limit |
| A keyed-batch question never references `` `item` `` | warning | warning | It would be judged against the whole batch state, likely not what you want |
| Text matching a known secret pattern (OpenRouter, Anthropic, AWS, GitHub, Google, or Slack keys/tokens; a private-key block) anywhere in state, instructions, or criteria | **secret — always blocks** | still blocks | Never sent; only the kind of match is printed, never the match itself |
| State + longest question, or the whole request, over the provider's token limit | error (raises immediately) | still blocks | Not a lint finding — a structural `InputError`; split the request or use `batch` |

## Environment variables

| Variable | Meaning |
| --- | --- |
| `OPENROUTER_API_KEY` | key for the default `openrouter` provider |
| `TYPESAFE_API_KEY` | key for `--provider typesafe` |
| `JEV_PROVIDER` | default `--provider` |
| `JEV_MODEL` | default `--model` |
| `JEV_APP_URL` | optional `HTTP-Referer` header sent to OpenRouter |
| `JEV_APP_NAME` | optional `X-Title` header sent to OpenRouter |
| `JEV_LEDGER` | usage ledger path (default `~/.config/jev/usage.jsonl`) |
| `JEV_NO_LEDGER` | `1`/`true`/`yes` disables the usage ledger |
| `JEV_CACHE_DIR` | answer cache directory (default `~/.cache/jev`) |

None of these are needed for `--dry-run` or `--mock`.

## Exit codes

| Command | 0 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| `ask` | ok | bad input, lint error, or API 400/404/422 | auth/credit | API/network | a question got no answer or an invalid one |
| `batch` / `rank` / `eval` | all items answered | bad input, lint error, or 404 | auth/credit | — | some rows errored (includes network failures and invalid answers per item — check the error rows) |
| `doctor` | ok | API 400/404/422 (e.g. bad model slug) | auth/credit | network/API error | — |
| `usage` | ok (including no ledger or no matching rows) | malformed `--since` or unreadable ledger | — | — | — |

`Ctrl-C` exits `130` for any command. A `4xx`/`5xx` from the API is retried
automatically for `408`, `429`, `500`, `502`, `503`, `504` and `529`, with
exponential backoff honoring `retry-after-ms`, then `retry-after` (seconds or
an HTTP date; a requested wait over 60 s falls back to normal backoff).
`400`, `401`, `402`, `403`, `404` and `422` are not retried; a `400 Unknown
model` stops the whole job like a `404`, and a `422` is shown as
`field.path: message`. Error messages include TypeSafe's
`x-typesafe-request-id` when the response carries one.

Redirects are never followed (a redirected request would carry the
`Authorization` header to another host); a `3xx` stops the job as a
configuration error. Response bodies over 8 MB are refused.

## Providers and limits

| | OpenRouter (default) | TypeSafe direct (`--provider typesafe`) |
| --- | --- | --- |
| Endpoint | `POST https://openrouter.ai/api/alpha/decisions` | `POST https://api.typesafe.ai/v1/systemone` |
| Model slug | `typesafe/jev-1.13` | `jev-1.13.0` |
| Key | `OPENROUTER_API_KEY` (needs prepaid credit) | `TYPESAFE_API_KEY` |
| Context | 32,000 tokens, state + all questions combined | 64,000 tokens total; 32,000 for state + the single longest question |
| Balance/usage endpoint | Yes (`doctor` reports it) | No — the local ledger is the only spend record |

Token counts throughout the CLI (dry-run estimates, the 8,000-token "large
state" warning, and limit enforcement) are a rough `len(text) // 4` estimate,
not an exact tokenizer count. Full request/response shapes, error codes, and
plain HTTP examples: [`skills/jev/references/api.md`](../skills/jev/references/api.md).
