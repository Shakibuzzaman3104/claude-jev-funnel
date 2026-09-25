# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `skills/jev/references/agents.md`: using Jev inside loops that act (browser
  agents, robots, drones, games, coding-agent guards). Covers rules first with
  Jev as a reviewer (11/11 vs 10/11 on a robot arm), rebuilding the candidate
  list every turn, never blocking on or applying a stale answer (sequence
  numbers, staleness tags, deadlines), putting the deciding facts in state,
  verifying "done" independently, restart-safe spend caps, using an LLM only for
  typed text, harness-side memory, jev-claude's measured wording and threshold
  lessons, and caveats on the official LangChain middleware and
  fast-jev-compaction.
- Question design: measured wording costs (direct vs compound vs padded
  criteria), polarity, silent truncation, and caching per item under packing.
- API notes: official Python SDK shapes (`Score(criteria=[...])`, `NoulAnswer`
  has no confidence, `r.nouls`/`r.choices`/`r.scores`) and large question maps.
- SKILL.md: one band for an answer's meaning, with risk deciding the action;
  scores rarely reach their top level.

## [1.1.0] - 2026-09-25

### Added

- `jev.py` validates every answer against the question it sent: matching
  `type`, a `choice` from the offered options, probabilities in [0, 1] that sum
  to 1 with the choice on top, and a `score` in range. In `batch`/`rank`/`eval`
  an invalid answer becomes an error row (never banded, never cached); a single
  request prints the problem and exits `5`.
- Skill guidance adapted from Keel's `jev-core` client: a bounded action
  selector (`choice` with `escalate` plus a per-candidate `fit` noul, acting
  only when both clear their bars), fail-closed response validation, no-retry
  call budgets with named fallback reasons on interactive paths, content-free
  decision traces, and untrusted-data wording for instructions.

- `jev.py` transport hardening, following the official SDKs and other Jev
  clients: redirects are refused (they would resend the key to another host),
  response bodies are capped at 8 MB, `408` is retried, `retry-after-ms` and
  HTTP-date `retry-after` are honoured (waits over 60 s fall back to backoff),
  `400 Unknown model` is treated as a fatal slug error, `422` bodies are shown as
  `field.path: message`, and `x-typesafe-request-id` is appended to errors.
- `rank` warns when `--top` cuts through a tie (answers are rounded and pile up
  near 0.99).
- Token estimates count CJK and Hangul characters at about one token each.
- Skill docs: measured findings from about 25 public Jev projects and
  evaluations: injection results (authority claims beat blunt commands; gates
  can be jammed; a confidence floor is a costly detector), option order and
  option-name effects, rounding and ties, per-type miscalibration, thresholds
  that don't transfer between datasets, language effects, packing effects,
  choice squashing stated probabilities, tournament vs independent-score
  routing, rerank fusion, per-request billing overhead, the official SDKs'
  retry behaviour, and a safety-gate recipe (deny rules → allowlist → Jev,
  minimal gate state, fork-safe PR workflow).

### Changed

- `ask` exits `5` (was `0`) when a question gets no answer back.

### Fixed

- The docs' response-validation advice compared the response `model` to the
  pinned slug exactly; live responses name a dated snapshot
  (`typesafe/jev-1.13-20260917`), so the check now matches by prefix.
- The skill no longer recommends adding a nonce to measure variance: a nonce
  itself moved answers in a 50-call test. It also no longer claims batched
  questions give the same answers as one-at-a-time calls; they give similar
  ones.

## [1.0.0] - 2026-09-24

First public release.

### Added

- `jev` skill (`skills/jev/SKILL.md`) for Claude Code: judge items in a
  session (Mode A) or design a Jev integration into software (Mode B).
- `skills/jev/scripts/jev.py`, a dependency-free Python 3.9+ CLI with six
  commands: `ask` (default, no subcommand), `batch`, `rank`, `eval`,
  `doctor`, `usage`.
- Reference docs for question design, ready-made recipes, and language
  integration: `skills/jev/references/{api,question-design,recipes,kotlin,swift}.md`.
- Built-in request linting: structural validation, a required no-match option
  on every `choice`, descriptive-score-level checks, positional-reference
  warnings, and secret scanning that refuses to send common API-key/token
  patterns — never relaxed by `--lenient`.
- A shared `batch`/`rank`/`eval` engine with a local SQLite answer cache;
  `batch --out` appends as it goes, resumes on rerun, and compacts
  atomically. A local cost ledger backs `jev.py usage`.
- Claude Code plugin packaging (`.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`) for one-command marketplace install.
- Worked examples: `examples/review-triage` (batch), `examples/rank-candidates`
  (rank), `examples/eval` (eval), all runnable offline with `--mock`.
- An offline unit test suite (`tests/test_jev.py`, `urlopen` mocked) and CI
  across Python 3.9, 3.11 and 3.13.
- Full CLI reference (`docs/cli.md`), contribution guide, and security policy.
- A reproducible live throughput benchmark (`benchmarks/`) and README
  diagrams (`docs/assets/`).

[Unreleased]: https://github.com/Shakibuzzaman3104/claude-jev-funnel/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Shakibuzzaman3104/claude-jev-funnel/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Shakibuzzaman3104/claude-jev-funnel/releases/tag/v1.0.0
