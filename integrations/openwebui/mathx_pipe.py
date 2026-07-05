"""
title: mathx
author: dan mackinlay
description: maj@k solving, claim checking, and argue claim-ledgers from mathx, with live progress streamed into chat.
requirements: mathx @ git+https://github.com/danmackinlay/mathx
version: 0.1.0
license: MIT
"""

# Open WebUI Pipe Function (upload via Admin Panel → Functions). Exposes three
# entries in the model picker: mathx · solve / check / argue. The loop runs in
# code — deterministic, independent of the served model's tool-calling
# competence — and argue's on_event stream maps onto Open WebUI's status
# emitter, so the ledger's progress narrates live in chat (ROADMAP Stage 5:
# the Pipe is the primary Open WebUI integration; MCP is the agent-client path).
#
# Provider settings come from mathx profiles (mathx.toml or
# ~/.config/mathx/config.toml, as seen by the Open WebUI process) or from
# MATHX_* environment variables; set the profile name in the valve.

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from mathx import config, ledger
from mathx.argue import argue
from mathx.check import check, check_result_to_dict
from mathx.engine import result_to_dict, solve
from mathx.report import render_check_report, render_ledger, render_report


def _last_user_text(body: dict) -> str:
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):  # multimodal content parts
            return " ".join(
                part.get("text", "") for part in content if part.get("type") == "text"
            ).strip()
    return ""


class Pipe:
    class Valves(BaseModel):
        PROFILE: str = Field(
            default="", description="mathx profile name (empty = MATHX_* environment)"
        )
        SOLVE_K: int = Field(default=16, description="samples for solve votes")
        SOLVE_MAX_K: int = Field(default=0, description="auto-escalation cap (0 = off)")
        CHECK_GRADE_K: int = Field(default=8, description="TRUE/FALSE graders per check")
        CHECK_TIR_K: int = Field(default=1, description="checker scripts per check")
        ARGUE_ROUNDS: int = Field(default=2, description="max refine cycles")
        ARGUE_GRADE_K: int = Field(default=4, description="graders per argue claim")
        ARGUE_TIR_K: int = Field(default=1, description="scripts per argue claim")

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [
            {"id": "solve", "name": "mathx · solve (maj@k vote)"},
            {"id": "check", "name": "mathx · check claim"},
            {"id": "argue", "name": "mathx · argue (claim ledger)"},
        ]

    async def pipe(self, body: dict, __event_emitter__=None):
        mode = (body.get("model") or "solve").rsplit(".", 1)[-1]
        prompt = _last_user_text(body)
        if not prompt:
            return "mathx: give me a problem (solve/argue) or a claim (check) as the message."

        loop = asyncio.get_running_loop()

        def status(description: str, done: bool = False) -> None:
            if __event_emitter__ is not None:
                loop.create_task(
                    __event_emitter__(
                        {"type": "status", "data": {"description": description, "done": done}}
                    )
                )

        try:
            p = config.resolve_provider(profile=self.valves.PROFILE or None)
        except ValueError as e:
            return f"mathx: {e}"

        common = dict(
            model=p["model"],
            base_url=p["base_url"],
            api_key=p["api_key"],
            temperature=p["temperature"],
            max_tokens=p["max_tokens"],
            top_p=p["top_p"],
            extra_body=p["extra_body"],
            max_retries=p["max_retries"],
        )

        try:
            if mode == "solve":
                status(f"fanning out {self.valves.SOLVE_K} samples…")
                result = await solve(
                    prompt,
                    k=self.valves.SOLVE_K,
                    max_k=self.valves.SOLVE_MAX_K or None,
                    equiv_judge_model=p["equiv_judge_model"],
                    on_sample=lambda s, done, planned: status(
                        f"sample {done}/{planned}" + (f" — {s.boxed}" if s.boxed else "")
                    ),
                    on_escalate=lambda margin, n: status(f"margin {margin} weak — escalating to k={n}"),
                    **common,
                )
                status("vote complete", done=True)
                return (
                    f"**answer:** ${result.answer}$ &nbsp; (margin {result.margin})\n\n"
                    "```\n" + render_report(result_to_dict(result)) + "\n```"
                )

            if mode == "check":
                status("checking claim (script + grade vote)…")
                result = await check(
                    prompt,
                    tir_k=self.valves.CHECK_TIR_K,
                    grade_k=self.valves.CHECK_GRADE_K,
                    meta_model=p["meta_model"],
                    **common,
                )
                status(f"verdict: {result.status}", done=True)
                return (
                    f"**{result.status}** — {result.summary}\n\n"
                    "```\n" + render_check_report(check_result_to_dict(result)) + "\n```"
                )

            if mode == "argue":
                led = await argue(
                    prompt,
                    rounds=self.valves.ARGUE_ROUNDS,
                    tir_k=self.valves.ARGUE_TIR_K,
                    grade_k=self.valves.ARGUE_GRADE_K,
                    meta_model=p["meta_model"],
                    on_event=lambda msg: status(msg),
                    **common,
                )
                status("ledger assembled", done=True)
                return (
                    f"{led['argument']}\n\n"
                    "```\n" + render_ledger(led, ledger.claim_state) + "\n```\n\n"
                    f"ledger id `{led['ledger_id']}` — inspect claims with "
                    f"`mathx show {led['ledger_id']}` or escalate with `mathx ledger recheck …`."
                )

            return f"mathx: unknown mode {mode!r} (expected solve/check/argue)"
        except (RuntimeError, ValueError) as e:
            status("failed", done=True)
            return f"mathx: {e}"
