# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Changed

- `ask` exits `5` (was `0`) when a question gets no answer back.

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

[1.0.0]: https://github.com/Shakibuzzaman3104/claude-jev-funnel/releases/tag/v1.0.0
