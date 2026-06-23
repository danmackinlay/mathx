"""mathx — a maths oracle for AI agents.

The agent dispatches `mathx solve "<problem>" --strategy maj@k --k 16 --out X.json`,
typically via background Bash; the file appears when the fan-out is done; the agent
reads the answer, margin, and audit trail.

Public API: `mathx.engine.solve(...)`. Everything else is plumbing.
"""

from mathx.engine import Result, Sample, solve

__all__ = ["solve", "Result", "Sample"]
