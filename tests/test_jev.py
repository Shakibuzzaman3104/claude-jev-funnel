#!/usr/bin/env python3
"""Offline test suite for jev.py. unittest, stdlib only.

Loads jev.py via importlib and also drives it as a subprocess. No network, no API key,
and no writes outside temp directories.
Run from the repo root: python3 -m unittest discover -s tests -v
"""

import contextlib
import email.message
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
JEV_PATH = os.path.join(REPO_ROOT, "skills", "jev", "scripts", "jev.py")
PLUGIN_MANIFEST = os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")


def _load_jev():
    spec = importlib.util.spec_from_file_location("jev_under_test", JEV_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


jev = _load_jev()

ENV_KEYS = (
    "OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "JEV_PROVIDER", "JEV_MODEL",
    "JEV_APP_URL", "JEV_APP_NAME", "JEV_LEDGER", "JEV_NO_LEDGER", "JEV_CACHE_DIR",
)

_module_state = {}


def _blocked_urlopen(request, timeout=None):
    raise AssertionError("tests must not reach the network; patch urlopen with patched_http()")


def setUpModule():
    """Point the ledger and cache at a temp dir and block real HTTP for the whole module."""
    _module_state["env"] = {key: os.environ.get(key) for key in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    tmp = tempfile.mkdtemp(prefix="jev-tests-")
    _module_state["tmp"] = tmp
    os.environ["JEV_NO_LEDGER"] = "1"
    os.environ["JEV_LEDGER"] = os.path.join(tmp, "usage.jsonl")
    os.environ["JEV_CACHE_DIR"] = os.path.join(tmp, "cache")
    patcher = mock.patch.object(jev.urllib.request, "urlopen", new=_blocked_urlopen)
    patcher.start()
    _module_state["patcher"] = patcher


def tearDownModule():
    _module_state["patcher"].stop()
    for key, value in _module_state["env"].items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(_module_state["tmp"], ignore_errors=True)


# ---------------------------------------------------------------- helpers

class CliResult:
    """stdout/stderr/exit code from one in-process CLI run."""

    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err

    def lines(self):
        return [line for line in self.out.splitlines() if line.strip()]

    def json_lines(self):
        return [json.loads(line) for line in self.lines()]


def run_cli(argv):
    """Run jev.main(argv) in-process, capturing stdout/stderr/exit code."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = jev.main(list(argv))
        code = result if isinstance(result, int) else 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return CliResult(code, out.getvalue(), err.getvalue())


def run_subprocess(argv, env_extra=None, input_text=None, timeout=20):
    """Run jev.py as a real subprocess with a clean environment."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.setdefault("PATH", os.environ.get("PATH", ""))
    tmp = _module_state.get("tmp") or tempfile.gettempdir()
    env["JEV_NO_LEDGER"] = "1"
    env["JEV_LEDGER"] = os.path.join(tmp, "subprocess-usage.jsonl")
    env["JEV_CACHE_DIR"] = os.path.join(tmp, "subprocess-cache")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, JEV_PATH] + list(argv),
        capture_output=True, text=True, env=env, input=input_text, timeout=timeout,
    )
    return proc


class FakeHTTPResponse:
    """Mimics the object returned by urllib.request.urlopen()."""

    def __init__(self, status, body_obj):
        self.status = status
        self._body = json.dumps(body_obj).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def make_http_error(status, retry_after=None, body=b"{}"):
    """A urllib.error.HTTPError with an optional retry-after header."""
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["retry-after"] = str(retry_after)
    return urllib.error.HTTPError(url="http://example.invalid", code=status, msg="err",
                                  hdrs=hdrs, fp=io.BytesIO(body))


class ScriptedUrlopen:
    """Replays a fixed sequence of responses/exceptions for urlopen()."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, request, timeout=None):
        with self.lock:
            self.calls.append(request)
            if not self.actions:
                raise AssertionError("ScriptedUrlopen ran out of scripted actions")
            action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def success(body_obj, status=200):
    return FakeHTTPResponse(status, body_obj)


def noul_body(model="typesafe/jev-1.13", qid="sky", value=0.9, input_tokens=10, cost=None):
    usage = {"input_tokens": input_tokens}
    if cost is not None:
        usage["cost"] = cost
    return {"model": model, "answers": {qid: {"type": "noul", "noul": value}}, "usage": usage}


class JevTestCase(unittest.TestCase):
    """Isolated env vars, temp dirs, no network by default."""

    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp = tempfile.mkdtemp(prefix="jev-test-")
        os.environ["JEV_NO_LEDGER"] = "1"
        os.environ["JEV_LEDGER"] = os.path.join(self.tmp, "usage.jsonl")
        os.environ["JEV_CACHE_DIR"] = os.path.join(self.tmp, "cache")
        self.addCleanup(self._restore_env)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _restore_env(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def path(self, name):
        return os.path.join(self.tmp, name)

    def write(self, name, content):
        full = self.path(name)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
        return full

    def write_json(self, name, obj):
        return self.write(name, json.dumps(obj))

    def write_jsonl(self, name, rows):
        return self.write(name, "\n".join(json.dumps(r) for r in rows) + "\n")


def patched_http(actions):
    """Context manager patching urlopen with a scripted sequence; sleep is a no-op."""
    scripted = ScriptedUrlopen(actions)
    patchers = [
        mock.patch.object(jev.urllib.request, "urlopen", new=scripted),
        mock.patch.object(jev.time, "sleep", new=lambda s: None),
    ]
    for patcher in patchers:
        patcher.start()
    return scripted, patchers


def unpatch(patchers):
    for patcher in reversed(patchers):
        patcher.stop()


# ================================================================== ask mode

class TestAskModeFlags(JevTestCase):
    def test_noul_flag_basic_mock(self):
        result = run_cli(["--state", "The sky is blue.", "--noul", "sky",
                          "Does the text say the sky is blue?", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("sky", result.out)
        self.assertIn("noul", result.out)

    def test_choice_flag_basic_mock(self):
        result = run_cli(["--state", "Refund request", "--choice", "topic",
                          "What is this about?", "billing=Billing question",
                          "shipping=Shipping question", "none=No listed option fits", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("choice", result.out)

    def test_score_flag_basic_mock(self):
        result = run_cli(["--state", "Great job today", "--score", "quality",
                          "Rate the quality", "Very poor performance overall",
                          "Excellent performance overall", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("score", result.out)

    def test_choice_needs_two_options(self):
        result = run_cli(["--state", "x", "--choice", "topic", "Q?", "onlyone"])
        self.assertEqual(result.code, 2)
        self.assertIn("--choice needs", result.err)

    def test_score_needs_two_levels(self):
        result = run_cli(["--state", "x", "--score", "q", "Q?", "onlylevel"])
        self.assertEqual(result.code, 2)
        self.assertIn("--score needs", result.err)

    def test_duplicate_question_id_across_flags(self):
        result = run_cli(["--state", "x", "--noul", "q", "Is it true?",
                          "--noul", "q", "Is it also true?", "--mock"])
        self.assertEqual(result.code, 2)
        self.assertIn("duplicate question id", result.err)

    def test_request_or_state_required(self):
        result = run_cli([])
        self.assertEqual(result.code, 2)
        self.assertIn("provide --request or --state", result.err)

    def test_request_inline_json(self):
        body = {"state": "hi", "model": "typesafe/jev-1.13",
                "questions": {"q": {"type": "noul", "instructions": "Is this a greeting?"}}}
        result = run_cli(["--request", json.dumps(body), "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("q", result.out)

    def test_request_from_file(self):
        body = {"state": "hi", "model": "typesafe/jev-1.13",
                "questions": {"q": {"type": "noul", "instructions": "Is this a greeting?"}}}
        path = self.write_json("req.json", body)
        result = run_cli(["--request", path, "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("q", result.out)

    def test_request_from_stdin(self):
        body = {"state": "hi", "model": "typesafe/jev-1.13",
                "questions": {"q": {"type": "noul", "instructions": "Is this a greeting?"}}}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(body))):
            result = run_cli(["--request", "-", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("q", result.out)

    def test_request_keeps_its_own_model_field(self):
        body = {"state": "hi", "model": "custom/model-x",
                "questions": {"q": {"type": "noul", "instructions": "Is this a greeting?"}}}
        result = run_cli(["--request", json.dumps(body), "--model", "typesafe/jev-1.13",
                          "--mock", "--json"])
        self.assertEqual(result.code, 0)
        # --mock answers with its own model name, so check the sent body via --dry-run.
        result2 = run_cli(["--request", json.dumps(body), "--model", "typesafe/jev-1.13", "--dry-run"])
        printed = json.loads(result2.out)
        self.assertEqual(printed["model"], "custom/model-x")

    def test_state_at_file(self):
        path = self.write("state.txt", "The sky is blue and the grass is green.")
        result = run_cli(["--state", "@" + path, "--noul", "q", "Does it mention the sky?",
                          "--mock"])
        self.assertEqual(result.code, 0)

    def test_state_from_stdin(self):
        with mock.patch("sys.stdin", io.StringIO("plain text state")):
            result = run_cli(["--state", "-", "--noul", "q", "Is this text plain?", "--mock"])
        self.assertEqual(result.code, 0)

    def test_state_json_object_parsed(self):
        result = run_cli(["--state", '{"a": 1}', "--noul", "q", "Does `a` equal one?", "--mock"])
        self.assertEqual(result.code, 0)

    def test_dry_run_prints_body_and_lints_first(self):
        result = run_cli(["--state", "x", "--noul", "q", "Is it true?", "--dry-run"])
        self.assertEqual(result.code, 0)
        body = json.loads(result.out)
        self.assertEqual(body["state"], "x")
        self.assertIn("dry run", result.err)

    def test_dry_run_fails_lint_before_printing(self):
        result = run_cli(["--state", "x", "--choice", "q", "Which?", "a=Option A", "b=Option B",
                          "--dry-run"])
        self.assertEqual(result.code, 2)
        self.assertEqual(result.out, "")
        self.assertIn("no no-match option", result.err)

    def test_json_flag_mock_round_trips_response(self):
        result = run_cli(["--state", "hi", "--noul", "q", "Is this a greeting?", "--mock", "--json"])
        data = json.loads(result.out)
        self.assertIn("answers", data)
        self.assertIn("usage", data)

    def test_mock_is_deterministic_for_same_body(self):
        argv = ["--state", "same text", "--noul", "q", "Is this the same text?", "--mock", "--json"]
        first = json.loads(run_cli(argv).out)
        second = json.loads(run_cli(argv).out)
        self.assertEqual(first["answers"], second["answers"])

    def test_mock_json_includes_latency_ms_field(self):
        result = run_cli(["--state", "hi", "--noul", "q", "Is this a greeting?", "--mock", "--json"])
        data = json.loads(result.out)
        self.assertIn("_latency_ms", data)
        self.assertIsInstance(data["_latency_ms"], int)

    def test_mock_json_has_same_key_set_as_live_json(self):
        mock_result = run_cli(["--state", "hi", "--noul", "q", "Is this a greeting?",
                               "--mock", "--json"])
        mock_data = json.loads(mock_result.out)
        self.assertEqual(set(mock_data), {"model", "answers", "usage", "_latency_ms"})

        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"
        scripted, patchers = patched_http([success(noul_body(qid="q", value=0.5))])
        try:
            live_result = run_cli(["--state", "hi", "--noul", "q", "Is this a greeting?", "--json"])
        finally:
            unpatch(patchers)
        live_data = json.loads(live_result.out)
        self.assertEqual(set(live_data), set(mock_data))
        self.assertIsInstance(live_data["_latency_ms"], int)

    def test_mock_plain_text_footer_includes_latency(self):
        result = run_cli(["--state", "hi", "--noul", "q", "Is this a greeting?", "--mock"])
        self.assertRegex(result.err, r"\(estimated\) \d+ ms")

    def test_score_note_printed_only_for_score_questions(self):
        with_score = run_cli(["--state", "x", "--score", "q", "Rate it",
                              "Very poor overall performance", "Excellent overall performance",
                              "--mock"])
        self.assertIn("least calibrated", with_score.err)
        without_score = run_cli(["--state", "x", "--noul", "q", "Is it true?", "--mock"])
        self.assertNotIn("least calibrated", without_score.err)


class TestProviderResolution(JevTestCase):
    def test_unknown_provider_rejected_by_argparse_choices(self):
        result = run_cli(["--state", "x", "--noul", "q", "Is it?", "--provider", "bogus"])
        self.assertEqual(result.code, 2)
        self.assertIn("invalid choice", result.err)

    def test_unknown_provider_via_env_rejected_by_resolve_provider(self):
        # --provider has argparse `choices`, so a bad value there is caught before resolve_provider
        # runs. JEV_PROVIDER has no such gate, so it exercises resolve_provider's own check.
        os.environ["JEV_PROVIDER"] = "bogus"
        result = run_cli(["--state", "x", "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("unknown provider", result.err)

    def test_provider_env_default(self):
        os.environ["JEV_PROVIDER"] = "typesafe"
        result = run_cli(["--state", "x", "--noul", "q", "Is it?", "--dry-run"])
        self.assertIn("typesafe", result.err)

    def test_model_env_override(self):
        os.environ["JEV_MODEL"] = "custom/env-model"
        result = run_cli(["--state", "x", "--noul", "q", "Is it?", "--dry-run"])
        body = json.loads(result.out)
        self.assertEqual(body["model"], "custom/env-model")


class TestProviderLimits(unittest.TestCase):
    def test_openrouter_limits_are_32k_both(self):
        provider = jev.PROVIDERS["openrouter"]
        self.assertEqual(provider["request_limit"], 32_000)
        self.assertEqual(provider["state_plus_question_limit"], 32_000)

    def test_typesafe_limits_64k_total_32k_pair(self):
        provider = jev.PROVIDERS["typesafe"]
        self.assertEqual(provider["request_limit"], 64_000)
        self.assertEqual(provider["state_plus_question_limit"], 32_000)

    def test_check_request_limits_pair_limit_enforced(self):
        big_state = "x" * (32_001 * 4)  # ~32001 tokens
        body = {"state": big_state, "model": "m",
                "questions": {"q": {"type": "noul", "instructions": "Is x present?"}}}
        with self.assertRaises(jev.InputError) as ctx:
            jev.check_request_limits(body, "openrouter")
        self.assertIn("state + longest question", str(ctx.exception))

    def test_check_request_limits_total_limit_enforced_on_typesafe(self):
        # state small, but many questions push total over 64k while pair stays under 32k
        state = "short state"
        questions = {}
        for i in range(40):
            questions[f"q{i}"] = {"type": "noul", "instructions": "z" * 8000}
        body = {"state": state, "model": "m", "questions": questions}
        with self.assertRaises(jev.InputError) as ctx:
            jev.check_request_limits(body, "typesafe")
        self.assertIn("limit 64000", str(ctx.exception))

    def test_ask_cli_rejects_oversize_state_on_openrouter(self):
        big = "word " * 20000  # ~100k chars => ~25k tokens, plus question pushes over pair limit
        result = run_cli(["--state", big * 2, "--noul", "q", "Is x present?", "--dry-run"])
        self.assertEqual(result.code, 2)
        self.assertIn("token", result.err)


# ================================================================== lint: structural

class TestLintStructural(JevTestCase):
    def test_missing_type(self):
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"instructions": "Is it?"})

    def test_bad_type(self):
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "banana", "instructions": "Is it?"})

    def test_missing_instructions(self):
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "noul"})

    def test_choice_needs_dict_criteria_with_2_plus(self):
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "choice", "instructions": "Which?",
                                               "criteria": {"a": "x"}})

    def test_choice_max_255_options(self):
        criteria = {f"o{i}": f"option {i}" for i in range(256)}
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "choice", "instructions": "Which?",
                                               "criteria": criteria})

    def test_choice_255_options_ok(self):
        criteria = {f"o{i}": f"option {i}" for i in range(255)}
        jev.check_question_structure("q", {"type": "choice", "instructions": "Which?",
                                           "criteria": criteria})  # should not raise

    def test_score_criteria_must_be_list(self):
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "score", "instructions": "Rate it",
                                               "criteria": {"a": "b"}})

    def test_score_needs_2_to_10_levels(self):
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "score", "instructions": "Rate it",
                                               "criteria": ["only one level here"]})
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "score", "instructions": "Rate it",
                                               "criteria": [f"level {i} description" for i in range(11)]})
        jev.check_question_structure("q", {"type": "score", "instructions": "Rate it",
                                           "criteria": [f"level {i} description" for i in range(10)]})

    def test_noul_criteria_only_true_false(self):
        jev.check_question_structure("q", {"type": "noul", "instructions": "Is it?",
                                           "criteria": {"true": "a", "false": "b"}})
        with self.assertRaises(jev.InputError):
            jev.check_question_structure("q", {"type": "noul", "instructions": "Is it?",
                                               "criteria": {"true": "a", "maybe": "c"}})

    def test_lenient_does_not_relax_structural_errors(self):
        result = run_cli(["--state", "x", "--score", "q", "Rate it", "onlyoneleveldescribed",
                          "--lenient"])
        self.assertEqual(result.code, 2)


# ================================================================== lint: no-match option

class TestLintNoMatchOption(JevTestCase):
    def test_missing_no_match_is_error(self):
        result = run_cli(["--state", "x", "--choice", "q", "Which?", "a=Option A", "b=Option B"])
        self.assertEqual(result.code, 2)
        self.assertIn("no no-match option", result.err)

    def test_allow_no_none_downgrades_to_warning(self):
        result = run_cli(["--state", "x", "--choice", "q", "Which?", "a=Option A", "b=Option B",
                          "--allow-no-none", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("warning", result.err)
        self.assertIn("no no-match option", result.err)

    def test_key_variants_detected(self):
        for key in ("none", "NONE", "no-match", "no_match", "nomatch", "not stated",
                    "not_applicable", "abstain"):
            self.assertTrue(jev.has_no_match_option({"a": "Option A", "b": "Option B", key: "X"}),
                            f"expected {key!r} to count as a no-match option")

    def test_phrase_in_description_detected(self):
        self.assertTrue(jev.has_no_match_option(
            {"a": "Option A", "b": "Option B", "z": "None of the listed options fit"}))
        self.assertTrue(jev.has_no_match_option(
            {"a": "Option A", "b": "Option B", "z": "Cannot be determined from the text"}))

    def test_no_false_positive_when_present(self):
        result = run_cli(["--state", "x", "--choice", "q", "Which?", "a=Option A", "b=Option B",
                          "none=None of the listed options fit", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertNotIn("no no-match option", result.err)

    def test_no_match_key_normalizes_dashes_and_spaces(self):
        self.assertTrue(jev.has_no_match_option({"a": "A", "b": "B", "no match": "unused"}))
        self.assertTrue(jev.has_no_match_option({"a": "A", "b": "B", "no-match": "unused"}))


# ================================================================== lint: score levels

class TestLintScoreLevels(JevTestCase):
    def test_bare_number_level_warns(self):
        result = run_cli(["--state", "x", "--score", "q", "Rate it", "1", "2", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("bare numbers or 1-2 words", result.err)

    def test_short_level_warns(self):
        result = run_cli(["--state", "x", "--score", "q", "Rate it", "Bad", "Great", "--mock"])
        self.assertIn("bare numbers or 1-2 words", result.err)

    def test_descriptive_levels_no_warning(self):
        result = run_cli(["--state", "x", "--score", "q", "Rate it",
                          "Consistently misses expectations across the board",
                          "Consistently exceeds expectations across the board", "--mock"])
        self.assertNotIn("bare numbers or 1-2 words", result.err)

    def test_is_weak_level_direct(self):
        self.assertTrue(jev.is_weak_level(3))
        self.assertTrue(jev.is_weak_level(3.5))
        self.assertTrue(jev.is_weak_level(True))
        self.assertTrue(jev.is_weak_level("42%"))
        self.assertTrue(jev.is_weak_level("Bad"))
        self.assertTrue(jev.is_weak_level("Pretty bad"))
        self.assertFalse(jev.is_weak_level("Consistently exceeds expectations"))


# ================================================================== lint: rating as noul

class TestLintRatingAsNoul(JevTestCase):
    def test_how_prefix_warns(self):
        result = run_cli(["--state", "x", "--noul", "q", "How happy is the customer?", "--mock"])
        self.assertIn("reads like a rating", result.err)

    def test_rate_prefix_warns(self):
        result = run_cli(["--state", "x", "--noul", "q", "Rate the sentiment of this text",
                          "--mock"])
        self.assertIn("reads like a rating", result.err)

    def test_to_what_extent_prefix_warns(self):
        result = run_cli(["--state", "x", "--noul", "q", "To what extent is this positive?",
                          "--mock"])
        self.assertIn("reads like a rating", result.err)

    def test_plain_yes_no_question_no_warning(self):
        result = run_cli(["--state", "x", "--noul", "q", "Is this text positive?", "--mock"])
        self.assertNotIn("reads like a rating", result.err)

    def test_however_does_not_false_positive_on_how_prefix(self):
        result = run_cli(["--state", "x", "--noul", "q",
                          "However this is phrased, is it about billing?", "--mock"])
        self.assertNotIn("reads like a rating", result.err)


# ================================================================== lint: broken/positional references

class TestLintReferences(JevTestCase):
    def test_bare_word_in_backticks_is_ignored(self):
        result = run_cli(["--state", '{"unrelated": 1}', "--noul", "q",
                          "Is the value `none` present?", "--mock"])
        self.assertNotIn("jev: warning", result.err)
        self.assertNotIn("jev: error", result.err)

    def test_broken_reference_errors_with_stop_segment(self):
        result = run_cli(["--state", '{"a": {"x": 1}}', "--noul", "q",
                          "Does `a.b.c` hold?"])
        self.assertEqual(result.code, 2)
        self.assertIn("does not resolve: stops at `b`", result.err)

    def test_full_resolution_no_finding(self):
        result = run_cli(["--state", '{"a": {"b": {"c": 1}}}', "--noul", "q",
                          "Does `a.b.c` hold?", "--mock"])
        self.assertNotIn("jev: warning", result.err)
        self.assertNotIn("jev: error", result.err)

    def test_string_state_suppresses_path_checks(self):
        result = run_cli(["--state", "plain text state, not an object", "--noul", "q",
                          "Does `foo.bar` hold?", "--mock"])
        self.assertNotIn("jev: warning", result.err)
        self.assertNotIn("jev: error", result.err)

    def test_instructions_object_keys_join_namespace(self):
        body = {
            "state": {"unrelated": 1}, "model": "m",
            "questions": {"q": {"type": "noul",
                                "instructions": {"question": "Does `note.flag` hold?",
                                                 "note": {"flag": True}}}},
        }
        result = run_cli(["--request", json.dumps(body), "--mock"])
        self.assertNotIn("note", result.err)
        self.assertEqual(result.code, 0)

    def test_positional_reference_warns_over_5_items(self):
        state = {"orders": list(range(10))}
        result = run_cli(["--state", json.dumps(state), "--noul", "q",
                          "Is `orders[6]` positive?", "--mock"])
        self.assertIn("indexes a list of 10 items by position", result.err)
        self.assertIn("29/320", result.err)

    def test_positional_reference_no_warning_under_5(self):
        state = {"orders": [1, 2, 3]}
        result = run_cli(["--state", json.dumps(state), "--noul", "q",
                          "Is `orders[1]` positive?", "--mock"])
        self.assertNotIn("indexes a list", result.err)

    def test_positional_reference_unresolvable_container(self):
        state = {"orders": {"not": "a list"}}
        result = run_cli(["--state", json.dumps(state), "--noul", "q",
                          "Is `orders[2]` positive?", "--mock"])
        self.assertIn("could not find that list", result.err)

    def test_positional_reference_root_missing_still_warns(self):
        result = run_cli(["--state", '{"a": 1}', "--noul", "q", "Is `bogus[2]` set?", "--mock"])
        self.assertIn("could not find that list", result.err)

class TestLintTemplateReferences(JevTestCase):
    # NOTE: "root not `item` fails to resolve -> error even in template mode" is exercised by
    # test_context_broken_reference_is_error below.
    def test_item_broken_reference_is_warning_not_error(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.missing` hold?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("checked against the first item", result.err)

    def test_context_broken_reference_is_error(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "context": {"foo": 1},
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` match `context.bar`?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--mock"])
        self.assertEqual(result.code, 2)
        self.assertIn("does not resolve", result.err)

    def test_keyed_pack_warns_when_item_never_mentioned(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Is the batch about billing?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--pack", "keyed", "--mock"])
        self.assertIn("never references `item`", result.err)

    def test_inline_pack_no_item_mention_warning(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Is the batch about billing?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--pack", "inline", "--mock"])
        self.assertNotIn("never references `item`", result.err)

    def test_naked_item_word_without_backticks_still_warns(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does item look suspicious?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--pack", "keyed", "--mock"])
        self.assertIn("never references `item`", result.err)

    def test_inline_pack_instructions_object_with_item_key_raises(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul",
                                "instructions": {"question": "Does `item.text` hold?", "item": "oops"}}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--pack", "inline", "--mock"])
        self.assertEqual(result.code, 2)
        self.assertIn("already have an 'item' key", result.err)


# ================================================================== lint: secrets

class TestLintSecrets(JevTestCase):
    def test_openrouter_key_pattern_in_state_errors_without_echo(self):
        secret = "sk-or-v1-" + "A" * 30
        result = run_cli(["--state", f"leaked key: {secret}", "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("OpenRouter API key", result.err)
        self.assertNotIn(secret, result.err)

    def test_anthropic_key_pattern(self):
        secret = "sk-ant-" + "B" * 30
        result = run_cli(["--state", secret, "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("Anthropic API key", result.err)
        self.assertNotIn(secret, result.err)

    def test_aws_key_pattern(self):
        secret = "AKIA" + "1234567890ABCDEF"
        result = run_cli(["--state", secret, "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("AWS access key", result.err)

    def test_github_token_pattern(self):
        secret = "ghp_" + "c" * 36
        result = run_cli(["--state", secret, "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("GitHub token", result.err)

    def test_google_api_key_pattern(self):
        secret = "AIza" + "D" * 35
        result = run_cli(["--state", secret, "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("Google API key", result.err)

    def test_private_key_block_pattern(self):
        secret = "-----BEGIN RSA PRIVATE KEY-----"
        result = run_cli(["--state", secret, "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("private key", result.err)

    def test_slack_token_pattern(self):
        secret = "xoxb-" + "1234567890"
        result = run_cli(["--state", secret, "--noul", "q", "Is it?"])
        self.assertEqual(result.code, 2)
        self.assertIn("Slack token", result.err)

    def test_secret_in_instructions_also_errors(self):
        secret = "sk-or-v1-" + "Z" * 25
        result = run_cli(["--state", "clean state", "--noul", "q", f"Does it mention {secret}?"])
        self.assertEqual(result.code, 2)
        self.assertNotIn(secret, result.err)

    def test_secret_in_batch_items_reports_ids_and_truncates(self):
        rows = [{"id": f"item{i}", "text": ("sk-or-v1-" + "Q" * 25) if i != 3 else "clean text"}
                for i in range(7)]
        items_path = self.write_jsonl("items.jsonl", rows)
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--mock"])
        self.assertEqual(result.code, 2)
        self.assertIn("item0", result.err)
        self.assertIn("...", result.err)
        self.assertNotIn("sk-or-v1-QQQQQ", result.err)


# ================================================================== bands

class TestBands(unittest.TestCase):
    def test_noul_bands(self):
        self.assertEqual(jev.band_for({"type": "noul", "noul": 0.8}), "YES")
        self.assertEqual(jev.band_for({"type": "noul", "noul": 1.0}), "YES")
        self.assertEqual(jev.band_for({"type": "noul", "noul": 0.2}), "NO")
        self.assertEqual(jev.band_for({"type": "noul", "noul": 0.0}), "NO")
        self.assertEqual(jev.band_for({"type": "noul", "noul": 0.5}), "UNCERTAIN")
        self.assertEqual(jev.band_for({"type": "noul", "noul": 0.79}), "UNCERTAIN")
        self.assertEqual(jev.band_for({"type": "noul", "noul": 0.21}), "UNCERTAIN")

    def test_choice_bands_on_p_max(self):
        self.assertEqual(jev.choice_band(0.8), "ACT")
        self.assertEqual(jev.choice_band(1.0), "ACT")
        self.assertEqual(jev.choice_band(0.5), "REVIEW")
        self.assertEqual(jev.choice_band(0.79), "REVIEW")
        self.assertEqual(jev.choice_band(0.49), "ABSTAIN")
        self.assertEqual(jev.choice_band(0.0), "ABSTAIN")

    def test_choice_band_via_full_answer(self):
        answer = {"type": "choice", "choice": "a", "confidence": 0.5,
                  "probabilities": {"a": 0.8, "b": 0.2}}
        self.assertEqual(jev.band_for(answer), "ACT")

    def test_score_bands_on_confidence(self):
        self.assertEqual(jev.band_for({"type": "score", "score": 1, "confidence": 0.8}), "CONFIDENT")
        self.assertEqual(jev.band_for({"type": "score", "score": 1, "confidence": 1.0}), "CONFIDENT")
        self.assertEqual(jev.band_for({"type": "score", "score": 1, "confidence": 0.5}), "REVIEW")
        self.assertEqual(jev.band_for({"type": "score", "score": 1, "confidence": 0.79}), "REVIEW")
        self.assertEqual(jev.band_for({"type": "score", "score": 1, "confidence": 0.49}), "UNSURE")

    def test_band_order_constants(self):
        self.assertEqual(jev.BAND_ORDER["noul"], ("YES", "NO", "UNCERTAIN"))
        self.assertEqual(jev.BAND_ORDER["choice"], ("ACT", "REVIEW", "ABSTAIN"))
        self.assertEqual(jev.BAND_ORDER["score"], ("CONFIDENT", "REVIEW", "UNSURE"))


# ================================================================== packing

class FakeEntry:
    """Minimal stand-in for a PackEntry, for probing build_state()."""

    def __init__(self, key, payload):
        self.key = key
        self.item = type("I", (), {"payload": payload})()


class TestPackingPrimitives(unittest.TestCase):
    def test_make_key_zero_padded_5_digits(self):
        self.assertEqual(jev.make_key(1), "k00001")
        self.assertEqual(jev.make_key(23), "k00023")
        self.assertEqual(jev.make_key(123456), "k123456")

    def test_rewrite_item_refs_bare(self):
        self.assertEqual(jev.rewrite_item_refs("Does `item` match?", "k00001"),
                         "Does `items.k00001` match?")

    def test_rewrite_item_refs_dotted(self):
        self.assertEqual(jev.rewrite_item_refs("Does `item.x` match?", "k00001"),
                         "Does `items.k00001.x` match?")

    def test_rewrite_item_refs_indexed(self):
        self.assertEqual(jev.rewrite_item_refs("Does `item[2]` match?", "k00001"),
                         "Does `items.k00001[2]` match?")

    def test_rewrite_item_refs_recurses_through_dict_and_list(self):
        value = {"a": "Is `item.x` ok?", "b": ["Is `item[2]` ok?", "no item ref here"]}
        rewritten = jev.rewrite_item_refs(value, "k00007")
        self.assertEqual(rewritten["a"], "Is `items.k00007.x` ok?")
        self.assertEqual(rewritten["b"][0], "Is `items.k00007[2]` ok?")
        self.assertEqual(rewritten["b"][1], "no item ref here")

    def test_item_questions_keyed_rewrites_instructions_and_criteria(self):
        template_questions = {
            "topic": {"type": "choice", "instructions": "What is `item.text` about?",
                     "criteria": {"a": "About `item.category`", "none": "None of these fit"}},
        }
        questions = jev.item_questions(template_questions, {"text": "hi"}, "k00001", "keyed")
        self.assertIn("k00001__topic", questions)
        q = questions["k00001__topic"]
        self.assertEqual(q["instructions"], "What is `items.k00001.text` about?")
        self.assertEqual(q["criteria"]["a"], "About `items.k00001.category`")

    def test_item_questions_inline_string_instructions(self):
        template_questions = {"is_crash": {"type": "noul", "instructions": "Does `item.text` crash?"}}
        payload = {"text": "boom"}
        questions = jev.item_questions(template_questions, payload, "k00003", "inline")
        q = questions["k00003__is_crash"]
        self.assertEqual(q["instructions"]["item"], payload)
        self.assertEqual(q["instructions"]["question"], "Does `item.text` crash?")

    def test_item_questions_inline_object_instructions_merge(self):
        template_questions = {
            "topic": {"type": "choice",
                     "instructions": {"question": "What about `item.text`?", "extra": 1},
                     "criteria": {"a": "A", "none": "None fit"}}}
        payload = {"text": "hi"}
        questions = jev.item_questions(template_questions, payload, "k00002", "inline")
        instructions = questions["k00002__topic"]["instructions"]
        self.assertEqual(instructions["item"], payload)
        self.assertEqual(instructions["extra"], 1)
        self.assertEqual(instructions["question"], "What about `item.text`?")

    def test_build_state_keyed_with_and_without_context(self):
        entries = [FakeEntry("k00001", {"text": "a"}), FakeEntry("k00002", {"text": "b"})]
        state_no_ctx = jev.build_state({"questions": {}}, "keyed", entries)
        self.assertNotIn("context", state_no_ctx)
        self.assertEqual(state_no_ctx["items"]["k00001"], {"text": "a"})
        state_ctx = jev.build_state({"questions": {}, "context": {"c": 1}}, "keyed", entries)
        self.assertEqual(state_ctx["context"], {"c": 1})

    def test_build_state_inline_uses_placeholder_text_or_context(self):
        state_no_ctx = jev.build_state({"questions": {}}, "inline", [])
        self.assertEqual(state_no_ctx, jev.INLINE_STATE_TEXT)
        state_ctx = jev.build_state({"questions": {}, "context": {"c": 1}}, "inline", [])
        self.assertEqual(state_ctx, {"context": {"c": 1}})


class TestPackItems(unittest.TestCase):
    def _items(self, n, size=10):
        return [jev.Item(i, i, {"text": "x" * size}) for i in range(n)]

    def test_per_request_cap_forces_multiple_requests(self):
        items = self._items(10)
        template = {"questions": {"q": {"type": "noul", "instructions": "Is `item.text` short?"}}}
        provider = {"request_limit": 1_000_000, "state_plus_question_limit": 1_000_000}
        requests, oversized = jev.pack_items(items, template, "keyed", "m", provider, per_request=3)
        self.assertEqual(oversized, [])
        self.assertEqual(len(requests), 4)  # 3,3,3,1
        sizes = sorted(len(r.entries) for r in requests)
        self.assertEqual(sizes, [1, 3, 3, 3])
        seen_items = sorted(entry[0].id for r in requests for entry in r.entries)
        self.assertEqual(seen_items, list(range(10)))

    def test_every_request_respects_hard_token_limits(self):
        items = self._items(50, size=50)
        template = {"questions": {"q": {"type": "noul", "instructions": "Is `item.text` short?"}}}
        provider = {"request_limit": 500, "state_plus_question_limit": 500}
        requests, oversized = jev.pack_items(items, template, "keyed", "m", provider, per_request=40)
        self.assertEqual(oversized, [])
        # check_request_limits() needs a provider *name* registered in PROVIDERS, so verify the
        # actual invariant (every packed request stays within the given provider's limits) directly.
        for request in requests:
            jev.check_request_shape(request.body)
            tokens = jev.estimate_tokens(request.body)
            self.assertLessEqual(tokens, provider["request_limit"])

    def test_oversized_item_becomes_error_row_not_a_request(self):
        big_item = jev.Item(0, "huge", {"text": "z" * 5000})
        items = [big_item]
        template = {"questions": {"q": {"type": "noul", "instructions": "Is `item.text` short?"}}}
        provider = {"request_limit": 100, "state_plus_question_limit": 100}
        requests, oversized = jev.pack_items(items, template, "keyed", "m", provider, per_request=40)
        self.assertEqual(requests, [])
        self.assertEqual(len(oversized), 1)
        self.assertIs(oversized[0][0], big_item)

    def test_packing_targets_80pct_not_100pct(self):
        # Build N identical items; find the smallest N causing a split, then show a request of
        # exactly N items would still fit under the 100% hard limit -- proving the split
        # happened before the hard limit, i.e. at the 80% target.
        template = {"questions": {"q": {"type": "noul", "instructions": "Is `item.text` short?"}}}
        provider = {"request_limit": 2000, "state_plus_question_limit": 1_000_000}
        payload = {"text": "y" * 40}

        def body_for_n(n):
            questions = {}
            entries = []
            for i in range(1, n + 1):
                key = jev.make_key(i)
                questions.update(jev.item_questions(template["questions"], payload, key, "keyed"))
                entries.append(FakeEntry(key, payload))
            state = jev.build_state(template, "keyed", entries)
            return {"state": state, "model": "m", "questions": questions}

        split_n = None
        for n in range(1, 60):
            items = [jev.Item(i, i, payload) for i in range(n)]
            requests, oversized = jev.pack_items(items, template, "keyed", "m", provider,
                                                 per_request=1000)
            self.assertEqual(oversized, [])
            if len(requests) > 1:
                split_n = n
                break
        self.assertIsNotNone(split_n, "packing never split within 60 identical items")
        chars_at_split = len(json.dumps(body_for_n(split_n), ensure_ascii=False))
        request_target = provider["request_limit"] * jev.PACK_TARGET * 4
        request_hard = provider["request_limit"] * 4
        self.assertGreater(chars_at_split, request_target,
                           "split should happen once the 80%% target is exceeded")
        self.assertLessEqual(chars_at_split, request_hard,
                             "the item that triggered a split would still fit under the 100%% hard limit")

    def test_pack_target_constant_is_80_percent(self):
        self.assertEqual(jev.PACK_TARGET, 0.8)


# ================================================================== items & templates loading

class TestItemLoading(JevTestCase):
    def test_json_array_mixed_element_types(self):
        path = self.write_json("items.json", [
            {"id": "a", "text": "A"}, "just text", 42, True, [1, 2]])
        items = jev.load_items(path, None)
        self.assertEqual(len(items), 5)
        self.assertEqual(items[0].id, "a")
        self.assertEqual(items[0].payload, {"text": "A"})
        self.assertEqual(items[1].payload, {"text": "just text"})
        self.assertEqual(items[2].payload, {"text": "42"})
        self.assertEqual(items[3].payload, {"text": "true"})
        self.assertEqual(items[4].payload, {"text": "[1, 2]"})

    def test_jsonl_dict_and_plaintext_lines_and_blank_skipped(self):
        text = '{"id": "a", "text": "A"}\n\nnot json at all\n"a json string line"\n123\n'
        path = self.write("items.jsonl", text)
        items = jev.load_items(path, None)
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0].payload, {"text": "A"})
        self.assertEqual(items[1].payload, {"text": "not json at all"})
        self.assertEqual(items[2].payload, {"text": "a json string line"})
        self.assertEqual(items[3].payload, {"text": "123"})

    def test_stdin_dash(self):
        with mock.patch("sys.stdin", io.StringIO('{"id": "a", "text": "A"}\n')):
            items = jev.load_items("-", None)
        self.assertEqual(len(items), 1)

    def test_duplicate_id_rejected(self):
        path = self.write_jsonl("items.jsonl", [{"id": "x", "text": "1"}, {"id": "x", "text": "2"}])
        with self.assertRaises(jev.InputError) as ctx:
            jev.load_items(path, None)
        self.assertIn("duplicate id", str(ctx.exception))

    def test_id_must_be_string_or_int(self):
        for bad_id in (True, 1.5, [1, 2], {"a": 1}):
            path = self.write_jsonl("items.jsonl", [{"id": bad_id, "text": "1"}])
            with self.assertRaises(jev.InputError):
                jev.load_items(path, None)

    def test_missing_id_uses_line_index(self):
        path = self.write_jsonl("items.jsonl", [{"text": "a"}, {"text": "b"}])
        items = jev.load_items(path, None)
        self.assertEqual([item.id for item in items], [0, 1])

    def test_fields_projects_subset(self):
        path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi", "extra": "drop me"}])
        items = jev.load_items(path, ["text"])
        self.assertEqual(items[0].payload, {"text": "hi"})

    def test_fields_missing_field_is_item_error(self):
        path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        items = jev.load_items(path, ["text", "missing_field"])
        self.assertIsNotNone(items[0].error)
        self.assertIn("missing field(s): missing_field", items[0].error)

    def test_item_with_no_fields_to_send_is_an_error(self):
        path = self.write_jsonl("items.jsonl", [{"id": "a"}])
        items = jev.load_items(path, None)
        self.assertIsNotNone(items[0].error)

    def test_no_items_found_raises(self):
        path = self.write("items.jsonl", "\n\n")
        with self.assertRaises(jev.InputError):
            jev.load_items(path, None)

    def test_load_template_context_and_questions_only(self):
        path = self.write_json("t.json", {"context": {"a": 1},
                                          "questions": {"q": {"type": "noul", "instructions": "Is it?"}},
                                          "junk": "ignored"})
        with contextlib.redirect_stderr(io.StringIO()) as err:
            template = jev.load_template(path)
        self.assertEqual(set(template), {"context", "questions"})
        self.assertIn("ignored", err.getvalue())

    def test_load_template_requires_questions(self):
        path = self.write_json("t.json", {"context": {}})
        with self.assertRaises(jev.InputError):
            jev.load_template(path)


# ================================================================== batch: CLI-level behaviors

class TestBatchStdoutIsJsonlOnly(JevTestCase):
    def test_stdout_has_only_valid_jsonl_rows(self):
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "hello"}, {"id": "b", "text": "world"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--mock"])
        self.assertEqual(result.code, 0)
        for line in result.lines():
            row = json.loads(line)  # raises if not valid JSON
            self.assertIn("id", row)
        self.assertIn("#", result.err)  # summary goes to stderr, not stdout

    def test_row_shape_success_and_error(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--mock"])
        row = result.json_lines()[0]
        self.assertEqual(set(row), {"id", "answers", "model", "cached"})
        self.assertIn("q", row["answers"])

    def test_dry_run_prints_first_body_and_stats(self):
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "hi"}, {"id": "b", "text": "there"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--dry-run"])
        self.assertEqual(result.code, 0)
        body = json.loads(result.out)
        self.assertIn("items", body["state"])
        self.assertIn("k00001", body["state"]["items"])
        self.assertIn("dry run", result.err)
        self.assertIn("requests", result.err)

    def test_batch_json_flag_not_supported(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path, "--json"])
        self.assertEqual(result.code, 2)  # argparse: unrecognized argument

    def test_both_items_and_template_stdin_rejected(self):
        result = run_cli(["batch", "--items", "-", "--template", "-", "--mock"])
        self.assertEqual(result.code, 2)
        self.assertIn("can't both read stdin", result.err)

    def test_engine_arg_validation(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--per-request", "0", "--mock"])
        self.assertEqual(result.code, 2)
        result2 = run_cli(["batch", "--items", items_path, "--template", template_path,
                           "--workers", "0", "--mock"])
        self.assertEqual(result2.code, 2)

    def test_fields_flag_via_cli(self):
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "hi", "junk": 1}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--fields", "text", "--dry-run"])
        body = json.loads(result.out)
        payload = body["state"]["items"]["k00001"]
        self.assertEqual(payload, {"text": "hi"})

    def test_mixed_valid_and_field_error_items(self):
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "hi"}, {"id": "b", "other": "no text field"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--fields", "text", "--mock"])
        self.assertEqual(result.code, 5)
        rows = {row["id"]: row for row in result.json_lines()}
        self.assertIn("answers", rows["a"])
        self.assertIn("error", rows["b"])
        self.assertIn("missing field(s): text", rows["b"]["error"])


class TestOutFileResumeRetryCompaction(JevTestCase):
    def _template(self):
        return self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})

    def test_append_then_resume_skips_answered(self):
        template_path = self._template()
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "one"}, {"id": "b", "text": "two"}])
        out_path = self.path("out.jsonl")
        first = run_cli(["batch", "--items", items_path, "--template", template_path,
                         "--out", out_path, "--mock"])
        self.assertEqual(first.code, 0)
        with open(out_path) as fh:
            first_rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual({r["id"] for r in first_rows}, {"a", "b"})

        # Add a 3rd item and re-run with --out: a and b should be skipped (resume), c answered.
        items_path2 = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "one"}, {"id": "b", "text": "two"}, {"id": "c", "text": "three"}])
        second = run_cli(["batch", "--items", items_path2, "--template", template_path,
                          "--out", out_path, "--mock"])
        self.assertEqual(second.code, 0)
        self.assertIn("resumed 2", second.err)
        with open(out_path) as fh:
            final_rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual({r["id"] for r in final_rows}, {"a", "b", "c"})
        self.assertEqual(len(final_rows), 3)  # compacted: one row per id

    def test_error_rows_are_retried_on_rerun(self):
        template_path = self._template()
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "one"}])
        out_path = self.path("out.jsonl")
        with open(out_path, "w") as fh:
            fh.write(json.dumps({"id": "a", "error": "simulated earlier failure"}) + "\n")
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--out", out_path, "--mock"])
        self.assertEqual(result.code, 0)
        with open(out_path) as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertIn("answers", rows[0])

    def test_compaction_preserves_input_order_and_last_wins(self):
        template_path = self._template()
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "b", "text": "two"}, {"id": "a", "text": "one"}])
        out_path = self.path("out.jsonl")
        # Pre-seed with a stale row for "a" and an extra id "z" not present in this run.
        with open(out_path, "w") as fh:
            fh.write(json.dumps({"id": "a", "error": "stale"}) + "\n")
            fh.write(json.dumps({"id": "z", "answers": {"q": {"type": "noul", "noul": 0.1}},
                                 "model": "x", "cached": False}) + "\n")
        result = run_cli(["batch", "--items", items_path, "--template", template_path,
                          "--out", out_path, "--mock"])
        self.assertEqual(result.code, 0)
        with open(out_path) as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        ids_in_order = [r["id"] for r in rows]
        # input order is b, a -- then any leftover ids (z) appended after.
        self.assertEqual(ids_in_order, ["b", "a", "z"])
        row_a = next(r for r in rows if r["id"] == "a")
        self.assertIn("answers", row_a)  # "a" was retried (was an error), no longer stale


class TestCache(JevTestCase):
    def _template(self):
        return self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})

    def test_mock_never_touches_cache(self):
        template_path = self._template()
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "one"}])
        run_cli(["batch", "--items", items_path, "--template", template_path, "--mock"])
        cache_file = jev.cache_path()
        self.assertFalse(os.path.exists(cache_file), "mock run must not create a cache file")

    def test_cache_hit_on_rerun_avoids_http_call(self):
        template_path = self._template()
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "one"}])
        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"
        scripted, patchers = patched_http([success(noul_body(qid="k00001__q", value=0.42))])
        try:
            first = run_cli(["batch", "--items", items_path, "--template", template_path])
        finally:
            unpatch(patchers)
        self.assertEqual(first.code, 0)
        self.assertEqual(len(scripted.calls), 1)
        row1 = first.json_lines()[0]
        self.assertFalse(row1["cached"])

        # sqlite file must now exist with one row
        self.assertTrue(os.path.exists(jev.cache_path()))

        scripted2, patchers2 = patched_http([])  # any HTTP call here should fail loudly
        try:
            second = run_cli(["batch", "--items", items_path, "--template", template_path])
        finally:
            unpatch(patchers2)
        self.assertEqual(second.code, 0)
        self.assertEqual(len(scripted2.calls), 0, "cache hit must not make an HTTP call")
        row2 = second.json_lines()[0]
        self.assertTrue(row2["cached"])
        self.assertEqual(row2["answers"], row1["answers"])

    def test_no_cache_flag_bypasses_cache(self):
        template_path = self._template()
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "one"}])
        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"
        scripted, patchers = patched_http([success(noul_body(qid="k00001__q", value=0.1))])
        try:
            run_cli(["batch", "--items", items_path, "--template", template_path])
        finally:
            unpatch(patchers)
        self.assertTrue(os.path.exists(jev.cache_path()))

        scripted2, patchers2 = patched_http([success(noul_body(qid="k00001__q", value=0.1))])
        try:
            result = run_cli(["batch", "--items", items_path, "--template", template_path,
                              "--no-cache"])
        finally:
            unpatch(patchers2)
        self.assertEqual(len(scripted2.calls), 1, "--no-cache must still call the API")

    def test_cache_key_uses_model_pack_template_payload(self):
        key1 = jev.cache_key("m1", "keyed", {"questions": {}}, {"text": "a"})
        key2 = jev.cache_key("m2", "keyed", {"questions": {}}, {"text": "a"})
        key3 = jev.cache_key("m1", "inline", {"questions": {}}, {"text": "a"})
        key4 = jev.cache_key("m1", "keyed", {"questions": {}}, {"text": "b"})
        self.assertEqual(len({key1, key2, key3, key4}), 4)
        self.assertEqual(jev.cache_key("m1", "keyed", {"questions": {}}, {"text": "a"}), key1)


# ================================================================== error handling (monkeypatched HTTP)

class TestErrorHandling(JevTestCase):
    def setUp(self):
        super().setUp()
        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"

    def _template(self):
        return self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` look ok?"}}})

    def test_429_with_retry_after_then_success_ask_mode(self):
        scripted, patchers = patched_http([
            make_http_error(429, retry_after="0"),
            success(noul_body(qid="sky", value=0.91)),
        ])
        try:
            result = run_cli(["--state", "The sky is blue.", "--noul", "sky",
                              "Does the text say the sky is blue?"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 0)
        self.assertEqual(len(scripted.calls), 2)
        self.assertIn("retrying", result.err)
        self.assertIn("YES", result.out)

    def test_500_exhausted_becomes_error_rows_and_exit_5(self):
        actions = [make_http_error(500) for _ in range(jev.MAX_ATTEMPTS)]
        scripted, patchers = patched_http(actions)
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "one"}])
        try:
            result = run_cli(["batch", "--items", items_path, "--template", self._template(),
                              "--workers", "1"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 5)
        self.assertEqual(len(scripted.calls), jev.MAX_ATTEMPTS)
        row = result.json_lines()[0]
        self.assertIn("error", row)
        self.assertIn("giving up after 5 attempts", row["error"])

    def test_400_is_per_request_only(self):
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "one"}, {"id": "b", "text": "two"}])
        actions = [make_http_error(400, body=b'{"error": "bad request"}'),
                  success(noul_body(qid="k00001__q", value=0.9))]
        scripted, patchers = patched_http(actions)
        try:
            result = run_cli(["batch", "--items", items_path, "--template", self._template(),
                              "--per-request", "1", "--workers", "1"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 5)
        rows = {row["id"]: row for row in result.json_lines()}
        self.assertIn("error", rows["a"])
        self.assertIn("400", rows["a"]["error"])
        self.assertIn("answers", rows["b"])

    def test_401_is_fatal_exit_3_finished_rows_kept(self):
        items_path = self.write_jsonl("items.jsonl", [
            {"id": "a", "text": "one"}, {"id": "b", "text": "two"}, {"id": "c", "text": "three"}])
        actions = [success(noul_body(qid="k00001__q", value=0.9)), make_http_error(401)]
        scripted, patchers = patched_http(actions)
        try:
            result = run_cli(["batch", "--items", items_path, "--template", self._template(),
                              "--per-request", "1", "--workers", "1"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 3)
        self.assertEqual(len(scripted.calls), 2, "the 3rd item's request must never be attempted")
        rows = {row["id"]: row for row in result.json_lines()}
        self.assertIn("answers", rows["a"])
        self.assertIn("error", rows["b"])
        self.assertIn("401", rows["b"]["error"])
        self.assertNotIn("c", rows, "item never sent gets no row at all")
        self.assertIn("not sent 1", result.err)
        self.assertIn("stopped early", result.err)

    def test_402_maps_to_fatal_exit_3(self):
        error = jev.api_error_for(402, "", "OPENROUTER_API_KEY")
        self.assertEqual(error.exit_code, 3)
        self.assertTrue(error.fatal)
        self.assertIn("402", error.message)

    def test_api_error_for_status_mapping(self):
        for status, expected_code, expected_fatal in (
            (401, 3, True), (402, 3, True), (403, 3, True), (404, 2, True),
            (400, 2, False), (422, 2, False), (418, 4, False),
        ):
            error = jev.api_error_for(status, "", "OPENROUTER_API_KEY")
            self.assertEqual(error.exit_code, expected_code, f"status {status}")
            self.assertEqual(error.fatal, expected_fatal, f"status {status}")

    def test_missing_api_key_is_fatal_exit_3(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        items_path = self.write_jsonl("items.jsonl", [{"id": "a", "text": "one"}])
        result = run_cli(["batch", "--items", items_path, "--template", self._template()])
        self.assertEqual(result.code, 3)
        self.assertIn("OPENROUTER_API_KEY is not set", result.err)

    def test_network_error_retries_then_fails(self):
        actions = [OSError("connection refused") for _ in range(jev.MAX_ATTEMPTS)]
        scripted, patchers = patched_http(actions)
        try:
            result = run_cli(["--state", "x", "--noul", "q", "Is it?"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 4)
        self.assertEqual(len(scripted.calls), jev.MAX_ATTEMPTS)


# ================================================================== ledger

class TestLedger(JevTestCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("JEV_NO_LEDGER", None)
        self.ledger_path = self.path("ledger.jsonl")
        os.environ["JEV_LEDGER"] = self.ledger_path
        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"

    def _read_ledger(self):
        with open(self.ledger_path) as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_reported_cost_row(self):
        scripted, patchers = patched_http([success(noul_body(qid="sky", value=0.9,
                                                             input_tokens=123, cost=0.00045))])
        try:
            result = run_cli(["--state", "The sky is blue.", "--noul", "sky",
                              "Does it say the sky is blue?", "--label", "mylabel"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 0)
        rows = self._read_ledger()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["cmd"], "ask")
        self.assertEqual(row["provider"], "openrouter")
        self.assertEqual(row["label"], "mylabel")
        self.assertEqual(row["questions"], 1)
        self.assertIsNone(row["items"])
        self.assertEqual(row["input_tokens"], 123)
        self.assertEqual(row["cost_source"], "reported")
        self.assertAlmostEqual(row["cost_usd"], 0.00045)
        self.assertEqual(row["status"], "ok")
        self.assertTrue(row["ts"].endswith("Z"))
        self.assertIsInstance(row["latency_ms"], int)
        with open(self.ledger_path) as fh:
            raw_text = fh.read()
        self.assertNotIn("fake-key-for-tests-only", raw_text)
        self.assertNotIn("The sky is blue.", raw_text)

    def test_estimated_cost_when_usage_lacks_cost(self):
        scripted, patchers = patched_http([success(noul_body(qid="sky", value=0.9,
                                                             input_tokens=1_000_000, cost=None))])
        try:
            run_cli(["--state", "x", "--noul", "sky", "Is it?"])
        finally:
            unpatch(patchers)
        row = self._read_ledger()[0]
        self.assertEqual(row["cost_source"], "estimated")
        self.assertAlmostEqual(row["cost_usd"], jev.PRICE_PER_MTOK_INPUT, places=6)

    def test_failed_attempts_are_ledgered_with_zero_cost(self):
        actions = [make_http_error(500) for _ in range(jev.MAX_ATTEMPTS)]
        scripted, patchers = patched_http(actions)
        try:
            run_cli(["--state", "x", "--noul", "sky", "Is it?"])
        finally:
            unpatch(patchers)
        rows = self._read_ledger()
        self.assertEqual(len(rows), jev.MAX_ATTEMPTS)
        for row in rows:
            self.assertEqual(row["status"], "http_500")
            self.assertEqual(row["cost_usd"], 0.0)

    def test_mock_writes_no_ledger_row(self):
        run_cli(["--state", "x", "--noul", "sky", "Is it?", "--mock"])
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_jev_no_ledger_disables(self):
        os.environ["JEV_NO_LEDGER"] = "1"
        scripted, patchers = patched_http([success(noul_body())])
        try:
            run_cli(["--state", "x", "--noul", "sky", "Is it?"])
        finally:
            unpatch(patchers)
        self.assertFalse(os.path.exists(self.ledger_path))


class TestUsageCommand(JevTestCase):
    def _write_ledger(self, rows):
        path = self.path("ledger.jsonl")
        with open(path, "w") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        os.environ["JEV_LEDGER"] = path
        return path

    def test_no_ledger_file(self):
        os.environ["JEV_LEDGER"] = self.path("does-not-exist.jsonl")
        result = run_cli(["usage"])
        self.assertEqual(result.code, 0)
        self.assertIn("no usage recorded yet", result.out)

    def test_group_by_day_default(self):
        self._write_ledger([
            {"ts": "2026-01-01T00:00:00Z", "cmd": "ask", "provider": "openrouter", "model": "m",
             "label": None, "cost_usd": 0.001, "cost_source": "reported", "input_tokens": 100,
             "status": "ok"},
            {"ts": "2026-01-01T05:00:00Z", "cmd": "ask", "provider": "openrouter", "model": "m",
             "label": None, "cost_usd": 0.002, "cost_source": "reported", "input_tokens": 200,
             "status": "http_500"},
            {"ts": "2026-01-02T00:00:00Z", "cmd": "ask", "provider": "openrouter", "model": "m",
             "label": None, "cost_usd": 0.003, "cost_source": "reported", "input_tokens": 300,
             "status": "ok"},
        ])
        result = run_cli(["usage"])
        self.assertEqual(result.code, 0)
        lines = {line.split()[0]: line.split() for line in result.out.splitlines() if line.strip()}
        self.assertIn("2026-01-01", lines)
        day1 = lines["2026-01-01"]
        self.assertEqual(day1[1], "2")  # calls
        self.assertEqual(day1[2], "1")  # errors
        self.assertIn("total", lines)

    def test_group_by_label_and_model(self):
        self._write_ledger([
            {"ts": "2026-01-01T00:00:00Z", "cmd": "batch", "provider": "openrouter", "model": "m1",
             "label": "alpha", "cost_usd": 0.001, "cost_source": "reported", "input_tokens": 10,
             "status": "ok"},
            {"ts": "2026-01-01T00:00:00Z", "cmd": "batch", "provider": "openrouter", "model": "m2",
             "label": "beta", "cost_usd": 0.002, "cost_source": "reported", "input_tokens": 20,
             "status": "ok"},
        ])
        by_label = run_cli(["usage", "--by", "label"])
        self.assertIn("alpha", by_label.out)
        self.assertIn("beta", by_label.out)
        by_model = run_cli(["usage", "--by", "model"])
        self.assertIn("m1", by_model.out)
        self.assertIn("m2", by_model.out)

    def test_since_filter(self):
        self._write_ledger([
            {"ts": "2026-01-01T00:00:00Z", "cmd": "ask", "provider": "openrouter", "model": "m",
             "label": None, "cost_usd": 0.001, "cost_source": "reported", "input_tokens": 10,
             "status": "ok"},
            {"ts": "2026-02-01T00:00:00Z", "cmd": "ask", "provider": "openrouter", "model": "m",
             "label": None, "cost_usd": 0.002, "cost_source": "reported", "input_tokens": 20,
             "status": "ok"},
        ])
        result = run_cli(["usage", "--since", "2026-01-15"])
        self.assertNotIn("2026-01-01", result.out)
        self.assertIn("2026-02-01", result.out)

    def test_label_filter_with_no_matches(self):
        self._write_ledger([
            {"ts": "2026-01-01T00:00:00Z", "cmd": "ask", "provider": "openrouter", "model": "m",
             "label": "alpha", "cost_usd": 0.001, "cost_source": "reported", "input_tokens": 10,
             "status": "ok"},
        ])
        result = run_cli(["usage", "--label", "nope"])
        self.assertEqual(result.code, 0)
        self.assertIn("no matching calls", result.out)

    def test_bad_since_date_rejected(self):
        result = run_cli(["usage", "--since", "not-a-date"])
        self.assertEqual(result.code, 2)


# ================================================================== rank

class TestRankCommand(JevTestCase):
    def _items(self):
        return self.write_jsonl("items.jsonl", [
            {"id": f"i{i}", "text": f"item number {i}"} for i in range(6)])

    def test_ordering_descending_by_probability(self):
        items_path = self._items()
        result = run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                          "--mock", "--top", "6"])
        self.assertEqual(result.code, 0)
        table_lines = [l for l in result.out.splitlines() if l.strip() and not l.startswith("rank")]
        probs = [float(l.split()[1]) for l in table_lines]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_top_limits_printed_rows(self):
        items_path = self._items()
        result = run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                          "--mock", "--top", "2"])
        table_lines = [l for l in result.out.splitlines() if l.strip() and not l.startswith("rank")
                      and not l.startswith("errors")]
        self.assertEqual(len(table_lines), 2)

    def test_out_writes_every_ranked_row_even_beyond_top(self):
        items_path = self._items()
        out_path = self.path("ranked.jsonl")
        result = run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                          "--mock", "--top", "2", "--min", "0", "--out", out_path])
        self.assertEqual(result.code, 0)
        with open(out_path) as fh:
            out_rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(out_rows), 6, "--out must write every ranked row, not just --top")
        ps = [row["p"] for row in out_rows]
        self.assertEqual(ps, sorted(ps, reverse=True))
        for row in out_rows:
            self.assertEqual(set(row), {"id", "p", "band"})

    def test_min_filters_rows(self):
        items_path = self._items()
        baseline_out = self.path("baseline.jsonl")
        run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                "--mock", "--min", "0", "--out", baseline_out])
        with open(baseline_out) as fh:
            baseline = [json.loads(l) for l in fh if l.strip()]
        ps = sorted(row["p"] for row in baseline)
        median = ps[len(ps) // 2]
        expected = [row for row in baseline if row["p"] >= median]

        out_path = self.path("ranked.jsonl")
        run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                "--mock", "--min", str(median), "--out", out_path])
        with open(out_path) as fh:
            out_rows = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(out_rows), len(expected))
        for row in out_rows:
            self.assertGreaterEqual(row["p"], median)
        self.assertLess(len(out_rows), len(baseline), "median split should drop at least one row")

    def test_default_pack_is_inline(self):
        items_path = self._items()
        result = run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                          "--dry-run"])
        body = json.loads(result.out)
        self.assertIsInstance(body["state"], str)  # inline placeholder text, not {"items": ...}

    def test_true_false_criteria_included(self):
        items_path = self._items()
        result = run_cli(["rank", "--items", items_path, "--question", "Is this about billing?",
                          "--true", "clearly billing-related", "--false", "not about billing",
                          "--dry-run"])
        body = json.loads(result.out)
        question = list(body["questions"].values())[0]
        self.assertEqual(question["criteria"]["true"], "clearly billing-related")
        self.assertEqual(question["criteria"]["false"], "not about billing")

    def test_top_and_min_validated(self):
        items_path = self._items()
        result = run_cli(["rank", "--items", items_path, "--question", "Q?", "--top", "0", "--mock"])
        self.assertEqual(result.code, 2)
        result2 = run_cli(["rank", "--items", items_path, "--question", "Q?", "--min", "1.5",
                           "--mock"])
        self.assertEqual(result2.code, 2)


# ================================================================== eval

class TestNormalizeExpected(unittest.TestCase):
    def setUp(self):
        self.noul_q = {"type": "noul", "instructions": "Is it?"}
        self.choice_q = {"type": "choice", "instructions": "Which?",
                         "criteria": {"a": "A", "b": "B", "none": "None of these"}}
        self.score_q = {"type": "score", "instructions": "Rate it",
                        "criteria": ["Very poor overall", "Middling overall", "Excellent overall"]}

    def test_noul_bool_and_numeric_and_string_forms(self):
        self.assertEqual(jev.normalize_expected("q", self.noul_q, True), True)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, False), False)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, 1), True)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, 0), False)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, "yes"), True)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, "NO"), False)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, "y"), True)
        self.assertEqual(jev.normalize_expected("q", self.noul_q, "0"), False)
        with self.assertRaises(jev.InputError):
            jev.normalize_expected("q", self.noul_q, "maybe")

    def test_choice_expects_a_key(self):
        self.assertEqual(jev.normalize_expected("q", self.choice_q, "a"), "a")
        with self.assertRaises(jev.InputError):
            jev.normalize_expected("q", self.choice_q, "nope")

    def test_score_expects_index_or_text(self):
        self.assertEqual(jev.normalize_expected("q", self.score_q, 0), 0)
        self.assertEqual(jev.normalize_expected("q", self.score_q, "Excellent overall"), 2)
        self.assertEqual(jev.normalize_expected("q", self.score_q, "1"), 1)
        with self.assertRaises(jev.InputError):
            jev.normalize_expected("q", self.score_q, "not a level")
        with self.assertRaises(jev.InputError):
            jev.normalize_expected("q", self.score_q, 99)


class TestEvalMetricsPure(unittest.TestCase):
    def test_noul_metrics_accuracy_calibration_bands_thresholds(self):
        cases = [
            (0.95, True), (0.9, True), (0.85, True), (0.8, True),
            (0.55, True), (0.55, False),
            (0.1, False), (0.05, False),
        ]
        metrics = jev.noul_metrics(cases, target=0.95)
        self.assertEqual(metrics["n"], 8)
        correct = sum(1 for p, label in cases if (p >= 0.5) == label)
        self.assertAlmostEqual(metrics["accuracy"], correct / 8)
        self.assertEqual(metrics["bands"]["YES"]["n"], 4)
        self.assertEqual(metrics["bands"]["NO"]["n"], 2)
        self.assertEqual(metrics["bands"]["UNCERTAIN"]["n"], 2)
        self.assertEqual(metrics["suggested"]["yes"]["threshold"], 0.6)
        self.assertAlmostEqual(metrics["suggested"]["yes"]["precision"], 1.0)
        self.assertAlmostEqual(metrics["suggested"]["yes"]["coverage"], 0.5)
        self.assertEqual(metrics["suggested"]["no"]["threshold"], 0.5)
        self.assertAlmostEqual(metrics["suggested"]["no"]["precision"], 1.0)
        self.assertIsNone(metrics["confidence_tracks_accuracy"])  # n=8 < MIN_CASES_FOR_TRACKING

    def test_noul_metrics_no_threshold_reaches_target(self):
        cases = [(0.6, True), (0.6, False), (0.9, True), (0.9, False)]
        metrics = jev.noul_metrics(cases, target=0.99)
        self.assertIsNone(metrics["suggested"]["yes"])

    def test_confidence_tracks_accuracy_true_when_monotonic(self):
        # far-from-0.5 (confident) predictions are all correct; near-0.5 predictions are a coin flip
        pairs = [(0.95, True)] * 8 + [(0.55, True), (0.55, False)] * 4
        self.assertTrue(jev.confidence_tracks_accuracy(pairs))

    def test_confidence_tracks_accuracy_false_when_inverted(self):
        pairs = [(0.95, False)] * 8 + [(0.55, True)] * 8
        self.assertFalse(jev.confidence_tracks_accuracy(pairs))

    def test_confidence_tracks_accuracy_none_when_too_few(self):
        self.assertIsNone(jev.confidence_tracks_accuracy([(0.9, True)] * 5))

    def test_choice_metrics(self):
        cases = [
            ({"choice": "a", "probabilities": {"a": 0.9, "b": 0.1}}, "a"),
            ({"choice": "a", "probabilities": {"a": 0.85, "b": 0.15}}, "a"),
            ({"choice": "b", "probabilities": {"a": 0.4, "b": 0.6}}, "a"),  # wrong, mid confidence
            ({"choice": "b", "probabilities": {"a": 0.1, "b": 0.9}}, "b"),
        ]
        metrics = jev.choice_metrics(cases, target=0.9)
        self.assertEqual(metrics["n"], 4)
        self.assertAlmostEqual(metrics["accuracy"], 0.75)
        self.assertEqual(metrics["bands"]["ACT"]["n"], 3)
        self.assertEqual(metrics["bands"]["REVIEW"]["n"], 1)

    def test_score_metrics(self):
        cases = [
            ({"score": 2.0, "confidence": 0.9}, 2),
            ({"score": 1.6, "confidence": 0.7}, 2),  # nearest_level rounds .5 up -> 2, exact
            ({"score": 0.0, "confidence": 0.3}, 2),  # way off
        ]
        metrics = jev.score_metrics(cases)
        self.assertEqual(metrics["n"], 3)
        self.assertAlmostEqual(metrics["exact_accuracy"], 2 / 3)
        self.assertAlmostEqual(metrics["mae"], (0 + 0.4 + 2.0) / 3)

    def test_nearest_level_rounds_half_up(self):
        self.assertEqual(jev.nearest_level(1.5), 2)
        self.assertEqual(jev.nearest_level(1.49), 1)
        self.assertEqual(jev.nearest_level(0.0), 0)


class TestEvalIntegration(JevTestCase):
    def setUp(self):
        super().setUp()
        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"

    def test_confidence_not_tracking_accuracy_warning_end_to_end(self):
        # 12 cases; make confident answers wrong and unsure answers right, per item, via one
        # packed request with per_request large enough to fit all in a single call.
        rows = []
        expects = []
        probs = []
        for i in range(6):
            rows.append({"id": f"hi{i}", "text": f"item {i}"})
            expects.append(False)  # true label is False
            probs.append(0.95)     # but model is very confident it's True -> wrong & confident
        for i in range(6):
            rows.append({"id": f"lo{i}", "text": f"item {i}"})
            expects.append(True)   # true label True
            probs.append(0.55)     # model is unsure, barely leaning True -> right & unsure
        cases = [dict(r, expect={"q": exp}) for r, exp in zip(rows, expects)]
        cases_path = self.write_jsonl("cases.jsonl", cases)
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` mention billing?"}}})

        answers = {f"k{idx+1:05d}__q": {"type": "noul", "noul": p} for idx, p in enumerate(probs)}
        response = {"model": "typesafe/jev-1.13", "answers": answers, "usage": {"input_tokens": 50}}
        scripted, patchers = patched_http([success(response)])
        try:
            result = run_cli(["eval", "--cases", cases_path, "--template", template_path,
                              "--per-request", "20", "--workers", "1"])
        finally:
            unpatch(patchers)
        self.assertEqual(result.code, 0)
        self.assertIn("confidence is not tracking accuracy", result.err)

        scripted2, patchers2 = patched_http([success(response)])
        try:
            json_result = run_cli(["eval", "--cases", cases_path, "--template", template_path,
                                   "--per-request", "20", "--workers", "1", "--json"])
        finally:
            unpatch(patchers2)
        report = json.loads(json_result.out)
        self.assertFalse(report["questions"]["q"]["confidence_tracks_accuracy"])
        self.assertEqual(report["cases"], 12)
        self.assertEqual(report["answered"], 12)

    def test_eval_mock_smoke_and_report_shape(self):
        cases = [{"id": f"c{i}", "text": f"item {i}", "expect": {"q": i % 2 == 0}} for i in range(6)]
        cases_path = self.write_jsonl("cases.jsonl", cases)
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` mention billing?"}}})
        result = run_cli(["eval", "--cases", cases_path, "--template", template_path, "--mock",
                          "--json"])
        self.assertEqual(result.code, 0)
        report = json.loads(result.out)
        self.assertEqual(set(report), {"cases", "answered", "errors", "target", "questions"})
        self.assertIn("q", report["questions"])

    def test_eval_target_validated(self):
        cases_path = self.write_jsonl("cases.jsonl", [{"id": "a", "text": "x", "expect": {"q": True}}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` mention billing?"}}})
        result = run_cli(["eval", "--cases", cases_path, "--template", template_path,
                          "--target", "0", "--mock"])
        self.assertEqual(result.code, 2)

    def test_eval_requires_at_least_one_expect(self):
        cases_path = self.write_jsonl("cases.jsonl", [{"id": "a", "text": "x"}])
        template_path = self.write_json("t.json", {
            "questions": {"q": {"type": "noul", "instructions": "Does `item.text` mention billing?"}}})
        result = run_cli(["eval", "--cases", cases_path, "--template", template_path, "--mock"])
        self.assertEqual(result.code, 2)
        self.assertIn("nothing to evaluate", result.err)


# ================================================================== doctor

class TestDoctorMock(JevTestCase):
    def test_doctor_mock_ok(self):
        result = run_cli(["doctor", "--mock"])
        self.assertEqual(result.code, 0)
        self.assertIn("provider  openrouter", result.out)
        self.assertIn("credit    skipped (--mock)", result.out)
        self.assertIn("ok", result.out.splitlines()[-1])

    def test_doctor_reports_key_presence_without_leaking_it(self):
        os.environ["OPENROUTER_API_KEY"] = "totally-fake-secret-value"
        result = run_cli(["doctor", "--mock"])
        self.assertIn("key       OPENROUTER_API_KEY set: yes", result.out)
        self.assertNotIn("totally-fake-secret-value", result.out)

    def test_doctor_no_key_not_mock_is_fatal(self):
        result = run_cli(["doctor"])
        self.assertEqual(result.code, 3)


# ================================================================== headers

class TestHeaders(JevTestCase):
    def test_user_agent_always_set(self):
        headers = jev.build_headers("openrouter", "k")
        self.assertTrue(jev.USER_AGENT)
        self.assertEqual(headers["User-Agent"], jev.USER_AGENT)

    def test_openrouter_optional_headers(self):
        os.environ["JEV_APP_URL"] = "https://example.com"
        os.environ["JEV_APP_NAME"] = "MyApp"
        headers = jev.build_headers("openrouter", "k")
        self.assertEqual(headers["HTTP-Referer"], "https://example.com")
        self.assertEqual(headers["X-Title"], "MyApp")

    def test_typesafe_never_gets_openrouter_headers(self):
        os.environ["JEV_APP_URL"] = "https://example.com"
        headers = jev.build_headers("typesafe", "k")
        self.assertNotIn("HTTP-Referer", headers)


# ================================================================== subprocess smoke tests

class TestSubprocessSmoke(unittest.TestCase):
    def test_help_for_every_subcommand(self):
        for args in (["--help"], ["batch", "--help"], ["rank", "--help"], ["eval", "--help"],
                    ["doctor", "--help"], ["usage", "--help"]):
            proc = run_subprocess(args)
            self.assertEqual(proc.returncode, 0, f"{args}: {proc.stderr}")
            self.assertIn("--", proc.stdout)

    def test_unknown_leading_token_falls_through_to_ask_and_errors(self):
        proc = run_subprocess(["totally-not-a-subcommand"])
        self.assertEqual(proc.returncode, 2)

    def test_batch_end_to_end_offline_subprocess(self):
        tmp = tempfile.mkdtemp(prefix="jev-subproc-")
        try:
            items_path = os.path.join(tmp, "items.jsonl")
            with open(items_path, "w") as fh:
                fh.write(json.dumps({"id": "a", "text": "hello"}) + "\n")
                fh.write(json.dumps({"id": "b", "text": "world"}) + "\n")
            template_path = os.path.join(tmp, "t.json")
            with open(template_path, "w") as fh:
                json.dump({"questions": {"q": {"type": "noul",
                                               "instructions": "Does `item.text` look ok?"}}}, fh)
            proc = run_subprocess(["batch", "--items", items_path, "--template", template_path,
                                   "--mock"], env_extra={"JEV_CACHE_DIR": os.path.join(tmp, "cache")})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [l for l in proc.stdout.splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)
            for line in lines:
                json.loads(line)
            self.assertIn("#", proc.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_module_importable_and_main_exists(self):
        proc = run_subprocess(["--state", "x", "--noul", "q", "Is it?", "--mock"])
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_version_flag_matches_plugin_manifest(self):
        with open(PLUGIN_MANIFEST, encoding="utf-8") as fh:
            manifest_version = json.load(fh)["version"]
        proc = run_subprocess(["--version"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"jev.py {manifest_version}")


# ================================================================== isolation

class TestIsolation(JevTestCase):
    def test_ledger_and_cache_point_at_temp_dirs(self):
        home = os.path.expanduser("~")
        for path in (jev.ledger_path(), jev.cache_path()):
            self.assertTrue(os.path.abspath(path).startswith(os.path.abspath(self.tmp)), path)
            self.assertFalse(path.startswith(os.path.join(home, ".config")), path)
            self.assertFalse(path.startswith(os.path.join(home, ".cache")), path)

    def test_unpatched_network_call_fails_loudly(self):
        os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests-only"
        with self.assertRaisesRegex(AssertionError, "must not reach the network"):
            run_cli(["--state", "x", "--noul", "q", "Is it true?"])


# ================================================================== bundled examples

EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")
LINT_FINDING = re.compile(r"jev: (warning|error)")
RANK_EXAMPLE_QUESTION = ("Does this search result give steps to fix a pip install that fails with "
                         "an SSL certificate verification error?")


@unittest.skipUnless(os.path.isdir(EXAMPLES_DIR), "examples/ not present")
class TestBundledExamples(JevTestCase):
    def example(self, *parts):
        return os.path.join(EXAMPLES_DIR, *parts)

    def assert_clean(self, result):
        self.assertEqual(result.code, 0, result.err)
        self.assertIsNone(LINT_FINDING.search(result.err), result.err)

    def test_review_triage_lints_clean_and_runs_mock(self):
        argv = ["batch", "--items", self.example("review-triage", "items.jsonl"),
                "--template", self.example("review-triage", "template.json")]
        self.assert_clean(run_cli(argv + ["--dry-run"]))
        result = run_cli(argv + ["--mock"])
        self.assert_clean(result)
        rows = result.json_lines()
        self.assertGreaterEqual(len(rows), 15)
        self.assertTrue(all("answers" in row for row in rows))

    def test_rank_candidates_lints_clean_and_runs_mock(self):
        argv = ["rank", "--items", self.example("rank-candidates", "items.jsonl"),
                "--question", RANK_EXAMPLE_QUESTION]
        self.assert_clean(run_cli(argv + ["--dry-run"]))
        self.assert_clean(run_cli(argv + ["--mock"]))

    def test_eval_cases_lint_clean_and_run_mock(self):
        argv = ["eval", "--cases", self.example("eval", "cases.jsonl"),
                "--template", self.example("eval", "template.json"), "--fields", "text"]
        self.assert_clean(run_cli(argv + ["--dry-run"]))
        result = run_cli(argv + ["--mock", "--json"])
        self.assertEqual(result.code, 0, result.err)
        report = json.loads(result.out)
        self.assertEqual(report["cases"], report["answered"])
        self.assertGreaterEqual(report["cases"], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
