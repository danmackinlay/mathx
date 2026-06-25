"""Expose ``mathx.solve`` as a Qwen-Agent tool.

Qwen-Agent's extension model is ``register_tool`` + ``BaseTool``. Since mathx is
itself a Python library, the natural integration is a direct import — no MCP
server, no subprocess, no JSON shuffling between processes.

Drop this file (or its registered class) into your Qwen-Agent project, install
``mathx`` (e.g. ``uv pip install -e ~/Source/mathx``), set ``MATHX_MODEL`` /
``MATHX_BASE_URL`` / ``MATHX_API_KEY`` in the environment, and reference the
tool by name when constructing the agent::

    from qwen_agent.agents import Assistant
    import examples.qwen_agent_tool  # registers the tool

    bot = Assistant(
        llm={"model": "qwen3-235b-thinking", "model_type": "oai",
             "model_server": "https://api.featherless.ai/v1"},
        function_list=["mathx_oracle"],
    )
    bot.run("Verify whether 7^999 mod 1000 = 143 using mathx_oracle.")

Synchronous note: ``call()`` blocks for the duration of the fan-out (minutes for
``--k 16``). Qwen-Agent's tool dispatch is itself synchronous, so this matches
the framework. If you need non-blocking dispatch, wire mathx via MCP instead
(see MCP_PLAN.md in the mathx repo).
"""
from __future__ import annotations

import asyncio
import json
import os

import json5
from qwen_agent.tools.base import BaseTool, register_tool

from mathx.engine import result_to_dict, solve


@register_tool("mathx_oracle")
class MathxOracle(BaseTool):
    description = (
        "Dispatch a hard maths problem (closed-form integral, modular "
        "exponentiation, olympiad inequality, verification of a claim) to the "
        "mathx oracle. Samples the problem k times against a maths-reasoning "
        "LLM, clusters answers by maths-equivalence, and returns the modal "
        "answer with a confidence margin. Use only when your own attempt has "
        "plausibly failed; the oracle costs tokens and minutes."
    )
    parameters = [
        {
            "name": "problem",
            "type": "string",
            "description": "The maths problem. LaTeX is fine.",
            "required": True,
        },
        {
            "name": "k",
            "type": "integer",
            "description": "Samples to draw. Default 16.",
            "required": False,
        },
        {
            "name": "strategy",
            "type": "string",
            "description": "cot | maj@k (default) | self_verify.",
            "required": False,
        },
    ]

    def call(self, params: str, **kwargs) -> str:
        args = json5.loads(params)
        model = os.environ.get("MATHX_MODEL")
        base_url = os.environ.get("MATHX_BASE_URL")
        api_key = os.environ.get("MATHX_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not (model and base_url and api_key):
            return json.dumps({
                "error": "set MATHX_MODEL, MATHX_BASE_URL and MATHX_API_KEY "
                         "(or OPENAI_API_KEY) before using mathx_oracle"
            })

        result = asyncio.run(solve(
            args["problem"],
            model=model,
            base_url=base_url,
            api_key=api_key,
            k=int(args.get("k", 16)),
            strategy=args.get("strategy", "maj@k"),
        ))
        return json.dumps(result_to_dict(result), ensure_ascii=False)
