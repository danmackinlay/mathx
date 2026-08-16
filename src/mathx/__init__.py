"""mathx — a maths oracle for AI agents.

The agent dispatches `mathx solve "<problem>" --strategy maj@k --k 16 --out X.json`,
typically via background Bash; the file appears when the fan-out is done; the agent
reads the answer, margin, and audit trail.

Public API: `mathx.solve(...)` and `mathx.check(...)`, plus the `ProviderConfig`
endpoint bundle both take. Everything else is plumbing.
"""

from mathx.check import CheckResult, check
from mathx.config import ProviderConfig
from mathx.engine import Result, Sample, solve

__all__ = ["solve", "check", "Result", "CheckResult", "Sample", "ProviderConfig"]
