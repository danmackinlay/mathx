"""Ledger store tests: records, claims, and live state derivation."""
from __future__ import annotations

import pytest

from mathx import jobs, ledger


def make_ledger(problem: str = "why is 6*7=42?") -> dict:
    return ledger.create(problem, model="m", base_url="http://b/v1", rounds_max=2)


class TestStore:
    def test_ledgers_dir_sits_beside_jobs(self, isolated_jobs_dir):
        assert ledger.ledgers_dir() == isolated_jobs_dir.parent / "ledgers"

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MATHX_LEDGERS_DIR", str(tmp_path / "elsewhere"))
        assert ledger.ledgers_dir() == tmp_path / "elsewhere"

    def test_create_read_roundtrip(self):
        led = make_ledger()
        assert led["kind"] == "ledger"
        assert led["status"] == "checking"
        assert ledger.read(led["ledger_id"]) == led

    def test_read_unknown_raises(self):
        with pytest.raises(KeyError, match="unknown ledger id"):
            ledger.read("20990101T000000Z-dead")

    def test_list_newest_updated_first(self):
        first = make_ledger("first")
        second = make_ledger("second")
        ledger.save(first)  # touching first makes it most recently updated
        listed = ledger.list_ledgers()
        assert [r["ledger_id"] for r in listed[:2]] == [first["ledger_id"], second["ledger_id"]]

    def test_add_and_get_claim(self):
        led = make_ledger()
        c1 = ledger.add_claim(led, "claim one", round_added=0)
        c2 = ledger.add_claim(led, "claim two", round_added=0, parent=c1["id"])
        assert (c1["id"], c2["id"]) == ("c1", "c2")
        assert c2["parent"] == "c1"
        assert ledger.get_claim(led, "c2") is c2
        with pytest.raises(KeyError, match="no claim 'c9'"):
            ledger.get_claim(led, "c9")


class TestClaimState:
    def test_lifecycle(self, fake_endpoint):
        led = make_ledger()
        claim = ledger.add_claim(led, "2+2=4", round_added=0)
        assert ledger.claim_state(claim) == ("unchecked", "")

        spawned = []
        job_id = ledger.attach_check(
            led, claim, api_key="k", round_=0, spawn=lambda jid, **kw: spawned.append(jid)
        )
        assert spawned == [job_id]
        assert ledger.claim_state(claim) == ("checking", "")

        jobs.finalize(job_id, result={"kind": "check", "status": "supported", "summary": "s — g"})
        assert ledger.claim_state(claim) == ("supported", "s — g")
        # persisted: a fresh read sees the verdict reference
        assert ledger.read(led["ledger_id"])["claims"][0]["verdicts"][0]["job_id"] == job_id

    def test_retired_and_error_and_missing(self):
        led = make_ledger()
        retired = ledger.add_claim(led, "old", round_added=0)
        retired["retired_round"] = 1
        assert ledger.claim_state(retired) == ("retired", "round 1")

        errored = ledger.add_claim(led, "e", round_added=0)
        job_id = ledger.attach_check(led, errored, api_key="k", round_=0, spawn=lambda *a, **k: None)
        jobs.fail(job_id, error="boom")
        assert ledger.claim_state(errored) == ("error", "boom")

        ghost = ledger.add_claim(led, "g", round_added=0)
        ghost["verdicts"].append({"job_id": "20990101T000000Z-dead", "round": 0, "kind": "check"})
        assert ledger.claim_state(ghost)[0] == "missing"

    def test_attach_check_model_override(self):
        led = make_ledger()
        claim = ledger.add_claim(led, "x", round_added=0)
        job_id = ledger.attach_check(
            led, claim, api_key="k", round_=0, model="stronger-model",
            spawn=lambda *a, **k: None,
        )
        args = jobs.read(job_id)["args"]
        assert args["model"] == "stronger-model"
        assert args["base_url"] == "http://b/v1"  # ledger default kept

    def test_state_counts(self):
        led = make_ledger()
        ledger.add_claim(led, "a", round_added=0)
        ledger.add_claim(led, "b", round_added=0)
        assert ledger.state_counts(led) == {"unchecked": 2}
