# Security Policy

## Supported versions

Only the latest `1.x` release is supported with security fixes.

| Version | Supported |
| --- | --- |
| 1.x | yes |

## Reporting a vulnerability

Please report privately rather than opening a public issue: use
[GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository (the **Security** tab → **Report a vulnerability**), which
opens a private security advisory with the maintainer. If that button is
missing, open a blank issue asking for a private contact, and include no
details.

Include what you found, steps to reproduce it, and the impact. You should get
an initial response within a few days.

## Key handling

- **Never commit an API key.** `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY`
  are read from the environment only — nothing in this repo reads a key from
  a file, and none should ever be added.
- **Set a credit limit** on your OpenRouter key
  (<https://openrouter.ai/settings/keys>). A leaked OpenRouter key can call
  every model on the account, not just Jev, which makes it more expensive to
  leak than a single-purpose key.
- **Never ship a key inside client code** — a mobile app, an APK/AAB, or a
  browser bundle. Anyone can extract it. Call Jev from your own backend
  instead, or, for content you already bundle, tag it once at build time with
  a batch job and ship the tags, not a live key. See
  [`skills/jev/references/api.md`](skills/jev/references/api.md),
  [`kotlin.md`](skills/jev/references/kotlin.md), and
  [`swift.md`](skills/jev/references/swift.md).

## What the CLI does to prevent leaks

`skills/jev/scripts/jev.py` scans every outgoing request — state, questions,
and criteria text — for common secret patterns (OpenRouter, Anthropic, AWS,
GitHub, Google, and Slack keys/tokens, and private-key blocks) before it is
sent, and refuses the request on a match. Only the *kind* of secret found is
ever printed, never the matched text itself. `--lenient` turns other lint
errors into warnings but never relaxes this check.
