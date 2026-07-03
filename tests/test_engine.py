"""Engine tests: the pure helpers, then solve() against the fake endpoint."""
from __future__ import annotations

import asyncio

from mathx.engine import (
    Result,
    Sample,
    _cluster_and_vote,
    _post_think,
    extract_boxed,
    result_to_dict,
    solve,
)

PROVIDER = dict(model="test-model", base_url="http://fake.test/v1", api_key="test-key")


def _solve(problem: str = "1+1?", **kwargs):
    return asyncio.run(solve(problem, **PROVIDER, **kwargs))


def _voted(boxed: str, confidence: float | None = None) -> Sample:
    return Sample(text=f"\\boxed{{{boxed}}}", boxed=boxed, confidence=confidence)


class TestExtractBoxed:
    def test_last_boxed_wins(self):
        assert extract_boxed(r"first \boxed{1}, finally \boxed{2}") == "2"

    def test_nested_braces(self):
        assert extract_boxed(r"\boxed{\frac{1}{2}}") == r"\frac{1}{2}"

    def test_deep_nesting_real_formulae(self):
        # regression: live e2e found formula answers nest 2+ deep, which the
        # old one-level regex silently dropped (15/16 abstentions)
        deep = r"\boxed{\log\frac{s_2}{s_1} + \frac{s_1^{2}+(\mu_1-\mu_2)^{2}}{2\,s_2^{2}} - \frac12}"
        assert extract_boxed(deep) == (
            r"\log\frac{s_2}{s_1} + \frac{s_1^{2}+(\mu_1-\mu_2)^{2}}{2\,s_2^{2}} - \frac12"
        )
        assert extract_boxed(r"\boxed{D_{\text{KL}}(P\|Q) = 3}") == r"D_{\text{KL}}(P\|Q) = 3"

    def test_escaped_braces_are_literal(self):
        assert extract_boxed(r"\boxed{\{1, 2\}}") == r"\{1, 2\}"

    def test_unterminated_box_falls_back_to_previous(self):
        assert extract_boxed(r"\boxed{42} then truncated \boxed{\frac{1}{") == "42"
        assert extract_boxed(r"\boxed{never closed") is None

    def test_missing_or_empty(self):
        assert extract_boxed("no box here") is None
        assert extract_boxed(r"\boxed{}") is None
        assert extract_boxed("") is None
        assert extract_boxed(None) is None


class TestPostThink:
    def test_strips_leading_think_block(self):
        assert _post_think("<think>mulling\nit over</think>\nanswer") == "answer"

    def test_noop_without_think(self):
        assert _post_think("plain answer") == "plain answer"

    def test_none_passthrough(self):
        assert _post_think(None) is None


class TestClusterAndVote:
    def test_simple_majority(self):
        winner, margin, votes = _cluster_and_vote([_voted("42"), _voted("42"), _voted("41")])
        assert winner == "42"
        assert margin == "2/3"
        assert votes == {"42": 2.0, "41": 1.0}

    def test_math_equivalence_clusters_together(self):
        winner, margin, votes = _cluster_and_vote([_voted("0.5"), _voted(r"\frac{1}{2}")])
        assert winner == "0.5"
        assert margin == "2/2"
        assert votes == {"0.5": 2.0}

    def test_latex_cosmetics_cluster_together(self):
        # regression from the live e2e: bare formula strings need $-wrapped
        # parsing or every typographic variant becomes a singleton cluster
        variants = [
            r"\log\frac{s_{2}}{s_{1}} + \frac{s_{1}^{2} + (\mu_{1} - \mu_{2})^{2}}{2\,s_{2}^{2}} - \frac{1}{2}",
            r"\log\!\left(\frac{s_2}{s_1}\right) + \frac{s_1^2 + (\mu_1-\mu_2)^2}{2 s_2^2} - \frac12",
        ]
        # a documented limit: math-verify does NOT unify everything — the \ln +
        # reordered-numerator variant stays a singleton (honest split, not a bug)
        holdout = r"\ln\frac{s_2}{s_1} + \frac{(\mu_1 - \mu_2)^2 + s_1^2}{2 s_2^2} - \frac{1}{2}"
        winner, margin, votes = _cluster_and_vote([_voted(v) for v in [*variants, holdout]])
        assert margin == "2/3"
        assert winner == variants[0]
        assert len(votes) == 2

    def test_confidence_weights_beat_counts(self):
        samples = [_voted("41", 0.1), _voted("41", 0.1), _voted("42", 0.9)]
        winner, margin, votes = _cluster_and_vote(samples)
        assert winner == "42"
        assert margin == "1/3"
        assert votes == {"42": 0.9, "41": 0.2}

    def test_unboxed_samples_do_not_vote(self):
        samples = [_voted("42"), Sample(text="stumped", boxed=None)]
        winner, margin, _ = _cluster_and_vote(samples)
        assert winner == "42"
        assert margin == "1/1"

    def test_no_votes_at_all(self):
        assert _cluster_and_vote([Sample(text=None, boxed=None)]) == (None, "0/0", {})


class TestSolve:
    def test_maj_at_k_votes(self, fake_endpoint):
        ep = fake_endpoint([r"\boxed{42}"] * 3 + [r"\boxed{41}"])
        r = _solve(k=4)
        assert r.answer == "42"
        assert r.margin == "3/4"
        assert r.votes == {"42": 3.0, "41": 1.0}
        assert r.k == 4
        assert r.problem == "1+1?"
        assert r.escalations == 0
        assert r.tokens_out_total == 4 * 20
        assert len(ep.requests) == 4
        assert ep.requests[0]["temperature"] == 0.7
        seeds = [req["seed"] for req in ep.requests]
        assert len(set(seeds)) == 4  # distinct per-sample seeds: no degenerate draws

    def test_cot_is_one_sample_at_t0(self, fake_endpoint):
        ep = fake_endpoint([r"\boxed{7}"])
        r = _solve(strategy="cot", k=16)
        assert r.answer == "7"
        assert r.k == 1
        assert ep.requests[0]["temperature"] == 0.0

    def test_errored_samples_do_not_vote(self, fake_endpoint):
        fake_endpoint([r"\boxed{42}", 400, r"\boxed{42}"])
        r = _solve(k=3)
        assert r.answer == "42"
        assert r.margin == "2/2"
        errors = [s for s in r.samples if s.error]
        assert len(errors) == 1 and "Error" in errors[0].error

    def test_self_verify_confidence_weighting(self, fake_endpoint):
        fake_endpoint(
            [r"\boxed{41}", r"\boxed{41}", r"\boxed{42}"],
            judge=lambda candidate: "0.9" if "42" in candidate else "0.1",
        )
        r = _solve(k=3, strategy="self_verify")
        assert r.answer == "42"
        assert r.votes == {"42": 0.9, "41": 0.2}
        assert sorted(s.confidence for s in r.samples) == [0.1, 0.1, 0.9]

    def test_on_sample_progress(self, fake_endpoint):
        fake_endpoint([r"\boxed{42}"] * 3)
        events = []
        _solve(k=3, on_sample=lambda s, done, planned: events.append((s.boxed, done, planned)))
        assert [(d, p) for _, d, p in events] == [(1, 3), (2, 3), (3, 3)]

    def test_escalates_on_weak_margin(self, fake_endpoint):
        # round 1 (k=4): 2/1/1 split — winner's share is exactly 1/2, no strict majority
        round1 = [r"\boxed{42}", r"\boxed{42}", r"\boxed{41}", r"\boxed{40}"]
        round2 = [r"\boxed{42}"] * 4
        fake_endpoint(round1 + round2)
        escalations = []
        r = _solve(k=4, max_k=8, on_escalate=lambda m, n: escalations.append((m, n)))
        assert r.answer == "42"
        assert r.k == 8
        assert r.escalations == 1
        assert r.margin == "6/8"
        assert escalations == [("2/4", 8)]

    def test_no_escalation_on_strong_margin(self, fake_endpoint):
        ep = fake_endpoint([r"\boxed{42}"] * 3 + [r"\boxed{41}"])
        r = _solve(k=4, max_k=16)
        assert r.k == 4
        assert r.escalations == 0
        assert not ep.replies  # exactly 4 calls made

    def test_escalation_stops_at_max_k(self, fake_endpoint):
        # a perpetual tie: never a strict majority, so only max_k halts it
        fake_endpoint([r"\boxed{41}", r"\boxed{42}"] * 2)
        r = _solve(k=2, max_k=4)
        assert r.k == 4
        assert r.escalations == 1
        assert r.margin == "2/4"

    def test_escalates_on_mass_abstention(self, fake_endpoint):
        # winner share is 1/1 = 100%, but 3 of 4 samples cast no vote:
        # quorum rule escalates anyway (regression from the live e2e)
        round1 = [r"\boxed{42}", "no box", "no box", "no box"]
        round2 = [r"\boxed{42}"] * 4
        fake_endpoint(round1 + round2)
        r = _solve(k=4, max_k=8)
        assert r.escalations == 1
        assert r.k == 8
        assert r.margin == "5/5"

    def test_no_escalation_without_any_answer(self, fake_endpoint):
        ep = fake_endpoint(["I am stumped.", "no idea"])
        r = _solve(k=2, max_k=8)
        assert r.answer is None
        assert r.k == 2  # a setup problem, not a split: don't burn more samples
        assert r.escalations == 0
        assert not ep.replies

    def test_progress_spans_escalation_rounds(self, fake_endpoint):
        fake_endpoint([r"\boxed{41}", r"\boxed{42}"] * 2)
        events = []
        _solve(k=2, max_k=4, on_sample=lambda s, done, planned: events.append((done, planned)))
        assert events == [(1, 2), (2, 2), (3, 4), (4, 4)]


class TestResultToDict:
    def test_shape(self):
        r = Result(
            answer="42",
            margin="1/1",
            votes={"42": 1.0},
            samples=[Sample(text="t", boxed="42", tokens_in=1, tokens_out=2, elapsed_ms=3)],
            strategy="maj@k",
            model="m",
            base_url="http://b/v1",
            k=1,
            problem="p",
            escalations=1,
        )
        d = result_to_dict(r)
        assert d["problem"] == "p"
        assert d["answer"] == "42"
        assert d["escalations"] == 1
        assert d["samples"] == [
            {
                "boxed": "42",
                "confidence": None,
                "error": None,
                "tokens_in": 1,
                "tokens_out": 2,
                "elapsed_ms": 3,
                "text": "t",
            }
        ]
