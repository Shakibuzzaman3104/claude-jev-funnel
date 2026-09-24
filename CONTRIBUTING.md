# Contributing

## Dev setup

No dependencies to install. Python 3.9+ and a clone of this repo are enough.

```bash
git clone https://github.com/Shakibuzzaman3104/claude-jev-funnel
cd claude-jev-funnel
```

## Run the tests

```bash
python3 -m unittest discover -s tests
```

The suite runs fully offline — `urlopen` is mocked, so no API key or network
access is needed, and no files are written outside temp directories.

## Run the examples

```bash
python3 skills/jev/scripts/jev.py batch --items examples/review-triage/items.jsonl \
  --template examples/review-triage/template.json --mock
python3 skills/jev/scripts/jev.py rank --items examples/rank-candidates/items.jsonl \
  --question "Does this search result give steps to fix a pip install that fails with an SSL certificate verification error?" \
  --mock
python3 skills/jev/scripts/jev.py eval --cases examples/eval/cases.jsonl \
  --template examples/eval/template.json --fields text --mock
```

`--mock` returns deterministic fake answers, so these run offline in CI too
(`.github/workflows/ci.yml`) — useful for checking that an example's items and
template still parse and pack correctly, not for judging real accuracy.

## Style

- Standard library only, in the CLI and in the tests — no third-party
  dependencies.
- Python 3.9 compatible: CI runs 3.9, 3.11 and 3.13, so don't rely on syntax
  or stdlib additions newer than 3.9.
- Human-readable function and variable names.
- Minimal comments — one line when a comment earns its place, not a
  paragraph explaining history or rationale. That belongs in the commit
  message or the PR description.

## Proposing a recipe or question-design change

`skills/jev/references/recipes.md` and `skills/jev/references/question-design.md`
carry measured claims with citation links (gists, official cookbooks, blog
posts, independent write-ups). If you're adding or changing one:

- Cite a source you can link to — don't state a benchmark number without one.
- Say what was actually measured (dataset size, setup) alongside the number,
  the way the existing entries do.
- Prefer a concrete "this doesn't help for X" callout backed by a measurement
  over general advice.
- If you're adding a new recipe, follow the existing shape: how to collect
  items, the request/template shape, and how to act on the answers in code.

## Release process

1. Bump `version` in `.claude-plugin/plugin.json` and `__version__` in
   `skills/jev/scripts/jev.py` (`tests/test_jev.py` checks that they match).
2. Add a new `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md`, plus its
   link reference at the bottom of the file.
3. Tag the release and push the tag:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. Create a GitHub release from the tag, with the CHANGELOG section as the
   notes.

## Reporting a bug or requesting a feature

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For a security
issue, see [`SECURITY.md`](SECURITY.md) instead of opening a public issue.
