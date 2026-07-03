"""Loop tests: decomposition parsing, then argue() end-to-end with the fake
endpoint and check workers run in-process."""
from __future__ import annotations

import asyncio
from itertools import count

import pytest

from mathx import jobs, ledger
from mathx.argue import DECOMPOSER_SYSTEM, argue, expand_claim, parse_decomposition
from mathx.check import GRADER_SYSTEM

PROVIDER = dict(model="test-model", base_url="http://fake.test/v1", api_key="test-key")


def decomp(*claims: str) -> str:
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    return f"ARGUMENT:\nBecause reasons.\nCLAIMS:\n{numbered}\n"


def sequence(*replies: str):
    n = count()
    return lambda _user: replies[min(next(n), len(replies) - 1)]


def run_argue(problem: str = "why?", **kwargs):
    kwargs.setdefault("tir_k", 0)
    kwargs.setdefault("grade_k", 1)
    kwargs.setdefault("poll_s", 0.01)
    kwargs.setdefault("spawn", jobs.spawn_worker)  # the inline_workers patch
    return asyncio.run(argue(problem, **PROVIDER, **kwargs))


class TestParseDecomposition:
    def test_canonical(self):
        argument, claims = parse_decomposition(decomp("First claim.", "Second claim."))
        assert argument == "Because reasons."
        assert claims == ["First claim.", "Second claim."]

    def test_lenient_bullets_and_missing_argument_header(self):
        text = "Some preamble.\nCLAIMS:\n- one\n* two\n3) three\n"
        argument, claims = parse_decomposition(text)
        assert argument == "Some preamble."
        assert claims == ["one", "two", "three"]

    def test_unparseable(self):
        assert parse_decomposition("no structure at all") is None
        assert parse_decomposition("CLAIMS:\nno numbered lines follow prose") is None
        assert parse_decomposition(None) is None


class TestArgue:
    def test_all_supported_first_round(self, fake_endpoint, inline_workers):
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: decomp("Claim alpha.", "Claim beta."),
            GRADER_SYSTEM: r"\boxed{TRUE}",
        })
        led = run_argue(rounds=2)
        assert led["status"] == "assembled"
        assert led["rounds_used"] == 0
        assert [ledger.claim_state(c)[0] for c in led["claims"]] == ["supported", "supported"]
        assert led["argument"] == "Because reasons."
        assert ledger.read(led["ledger_id"])["status"] == "assembled"  # persisted

    def test_refinement_retires_refuted_and_fills_scratchpad(self, fake_endpoint, inline_workers):
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: sequence(
                decomp("Claim BAD is true."),
                decomp("Claim GOOD is true."),
            ),
            GRADER_SYSTEM: lambda user: r"\boxed{FALSE}" if "BAD" in user else r"\boxed{TRUE}",
        })
        led = run_argue(rounds=2)
        assert led["status"] == "assembled"
        assert led["rounds_used"] == 1
        bad, good = led["claims"]
        assert bad["retired_round"] == 1
        assert ledger.claim_state(bad)[0] == "retired"
        assert ledger.claim_state(good)[0] == "supported"
        assert len(led["scratchpad"]) == 1
        assert "BAD" in led["scratchpad"][0]["claim"]

    def test_verbatim_claims_keep_verdicts_across_rounds(self, fake_endpoint, inline_workers):
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: sequence(
                decomp("Claim KEEP holds.", "Claim DROP holds."),
                decomp("Claim KEEP holds.", "Claim NEW holds."),
            ),
            GRADER_SYSTEM: lambda user: r"\boxed{FALSE}" if "DROP" in user else r"\boxed{TRUE}",
        })
        led = run_argue(rounds=1)
        keep = ledger.get_claim(led, "c1")
        assert len(keep["verdicts"]) == 1  # carried over, not re-checked
        assert len(jobs.list_jobs()) == 3  # KEEP, DROP, NEW — one check each

    def test_rounds_exhausted_leaves_honest_badges(self, fake_endpoint, inline_workers):
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: sequence(
                decomp("Claim STUBBORN one."),
                decomp("Claim STUBBORN two."),
            ),
            GRADER_SYSTEM: r"\boxed{FALSE}",
        })
        led = run_argue(rounds=1)
        assert led["status"] == "assembled"
        assert led["rounds_used"] == 1
        active = [c for c in led["claims"] if c["retired_round"] is None]
        assert [ledger.claim_state(c)[0] for c in active] == ["refuted"]

    def test_unparseable_decomposition_retries_then_fails(self, fake_endpoint, inline_workers):
        replies = iter(["nonsense", "more nonsense"])
        fake_endpoint(by_system={DECOMPOSER_SYSTEM: lambda _u: next(replies)})
        with pytest.raises(RuntimeError, match="decomposition failed twice"):
            run_argue()

    def test_events_narrate_the_loop(self, fake_endpoint, inline_workers):
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: decomp("Claim alpha."),
            GRADER_SYSTEM: r"\boxed{TRUE}",
        })
        events = []
        led = run_argue(on_event=events.append)
        assert events[0] == f"ledger: {led['ledger_id']}"
        assert any("round 0: decomposing" in e for e in events)
        assert any("c1 supported" in e for e in events)


class TestExpand:
    def test_adds_checked_children(self, fake_endpoint, inline_workers):
        fake_endpoint(by_system={
            DECOMPOSER_SYSTEM: decomp("Sub-claim one.", "Sub-claim two."),
        })
        led = ledger.create("p", model="test-model", base_url="http://fake.test/v1", rounds_max=2)
        parent = ledger.add_claim(led, "Big claim.", round_added=0)
        spawned = []
        children = asyncio.run(
            expand_claim(
                led, parent, api_key="test-key", tir_k=0, grade_k=1,
                spawn=lambda jid, **kw: spawned.append(jid),
            )
        )
        assert [c["parent"] for c in children] == [parent["id"]] * 2
        assert len(spawned) == 2
        persisted = ledger.read(led["ledger_id"])
        assert len(persisted["claims"]) == 3
