#!/usr/bin/env python3
"""Deterministic generator of synthetic app-review-style items for the throughput benchmark.

Usage: python3 make_items.py N > items.jsonl

Produces N JSONL rows {"id": "r0001", "text": "..."}. Text is 8-30 words,
templated from a mix of bug report / feature request / praise / vague
categories. No real product names are used. Fully deterministic for a given
N: seeded with random.Random(2026), so re-running produces the same file.
"""

import json
import random
import sys

SEED = 2026

BUG_TEMPLATES = [
    "The {feature} keeps {failure} whenever I try to {action}.",
    "Every time I {action}, the {feature} {failure} and I have to restart the app.",
    "Since the last update the {feature} {failure}, and {action} no longer works at all.",
    "I keep losing my {noun} because the {feature} {failure} without any warning.",
    "{feature} {failure} on my phone, so I can no longer {action} like before.",
]

FEATURE_TEMPLATES = [
    "It would be great if the app could {action} without needing to open the {feature} first.",
    "Please add a way to {action} directly from the {noun} screen, it is missing right now.",
    "Can you add {feature} support so I can {action} more easily than today?",
    "I wish there was an option to {action} automatically instead of doing it by hand every time.",
    "A dark mode for the {feature} would make it much easier to {action} at night.",
]

PRAISE_TEMPLATES = [
    "Really happy with the {feature}, it makes it so simple to {action} every day.",
    "This app is fantastic, the {feature} is fast and {action} feels effortless now.",
    "Been using it for months and the {feature} never lets me down when I {action}.",
    "Great {noun} experience overall, {action} is smooth and the {feature} just works.",
    "Love how clean the {feature} is, {action} takes seconds and nothing ever breaks.",
]

VAGUE_TEMPLATES = [
    "Not sure how I feel about the {feature}, it is fine I guess for {noun} stuff.",
    "The app is okay, {action} works most days but something about the {feature} feels off.",
    "Mixed feelings on this one, the {noun} side is decent but the {feature} could be better.",
    "Hard to say if the {feature} update helped, {action} feels about the same as before.",
    "It does what it says, nothing special about the {feature}, {action} is just average.",
]

CATEGORIES = [
    ("bug", BUG_TEMPLATES),
    ("feature", FEATURE_TEMPLATES),
    ("praise", PRAISE_TEMPLATES),
    ("vague", VAGUE_TEMPLATES),
]

FEATURES = [
    "sync feature", "search bar", "notification settings", "widget", "login screen",
    "calendar view", "export tool", "sharing option", "offline mode", "reminder system",
    "settings menu", "backup tool", "filter panel", "sort order", "account screen",
]

ACTIONS = [
    "add a new entry", "find an old note", "share it with a friend", "back up my data",
    "sign in on a new device", "sort my list", "set a reminder", "export my records",
    "switch between accounts", "update my profile", "check my history", "organize my files",
]

FAILURES = [
    "freezes", "crashes", "closes on its own", "gets stuck loading", "shows a blank screen",
    "stops responding", "logs me out", "resets my settings",
]

NOUNS = [
    "notes", "photos", "tasks", "contacts", "files", "records", "lists", "reminders",
]

CLOSERS = [
    "It has been like this for a couple of weeks now.",
    "Hoping this gets looked at soon.",
    "Happy to share more details if it helps.",
    "Just wanted to leave this feedback here.",
    "Figured it was worth mentioning.",
    "",  # allow some items to skip the closer entirely
]


def build_text(rng):
    category, templates = rng.choice(CATEGORIES)
    template = rng.choice(templates)
    text = template.format(
        feature=rng.choice(FEATURES),
        action=rng.choice(ACTIONS),
        failure=rng.choice(FAILURES),
        noun=rng.choice(NOUNS),
    )
    words = text.split()
    # Pad short sentences with a closer clause to reach the 8-30 word band,
    # and keep re-rolling the closer until the length fits.
    attempts = 0
    while len(words) < 8 and attempts < 8:
        closer = rng.choice([c for c in CLOSERS if c])
        text = f"{text} {closer}"
        words = text.split()
        attempts += 1
    while len(words) > 30 and attempts < 16:
        # Trim to the last full sentence under 30 words.
        text = " ".join(words[:30])
        if not text.endswith("."):
            text += "."
        words = text.split()
        attempts += 1
    if 8 <= len(words) <= 30:
        return category, text
    return None


def generate(n):
    rng = random.Random(SEED)
    items = []
    while len(items) < n:
        result = build_text(rng)
        if result is None:
            continue
        _category, text = result
        items.append(text)
    return items


def main():
    if len(sys.argv) != 2:
        print("usage: python3 make_items.py N > items.jsonl", file=sys.stderr)
        return 2
    n = int(sys.argv[1])
    for index, text in enumerate(generate(n), start=1):
        row = {"id": f"r{index:04d}", "text": text}
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
