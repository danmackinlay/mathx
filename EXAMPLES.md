# Worked examples

Two ML-flavoured walkthroughs that double as the end-to-end check. Both have exact ground
truth, so you can judge the tool, not just watch it. Run `mathx doctor` first if anything
errors before sampling.

## Point at a provider

Preferred: define profiles once (see README *Configuration*) and pass `--profile`:

```toml
# mathx.toml
[profiles.local]                     # a local server (vLLM / vllm-mlx / oMLX)
base_url = "http://localhost:8000/v1"
model = "vibethinker-bf16"           # specialist: solves and grades
meta_model = "qwen3.6-35b"           # generalist: decomposes, writes checker scripts
temperature = 1.0                    # VibeThinker wants hot sampling
top_p = 0.95
max_tokens = 6000
max_retries = 0
concurrency = 3                      # match the server's real parallelism

[profiles.cloud]                     # a cloud generalist via OpenRouter
base_url = "https://openrouter.ai/api/v1"
model = "deepseek/deepseek-v4-flash"
api_key_env = "OPENROUTER_API_KEY"
```

Bare env vars (`MATHX_MODEL` / `MATHX_BASE_URL` / `MATHX_API_KEY`) also work — add
`--profile local` or `--profile cloud` to the commands below, or omit it with env vars set.

Cost yardstick: example 1 ≈ 16–48 samples, example 2 ≈ 30–60 (including the argue round) —
minutes and modest tokens on a cloud endpoint; free but slower locally.

**Two lessons from running these live against a local server:**

- *Match the model to the role.* Reasoning-tuned math specialists are strong solvers and
  graders but can be hopeless at the meta-tasks — writing a checker script or emitting the
  ARGUMENT:/CLAIMS: decomposition (one spiralled for 16k tokens without producing a code
  fence). That's what a profile's `meta_model` key is for (as in `[profiles.local]` above):
  the specialist keeps the solve/grade seats, the generalist gets decomposition and script
  authorship, and `check`/`argue` route each task automatically. `--meta-model` overrides
  per call.
- *Pace the fan-out to the server.* A small local server admits a few concurrent generations
  and lets the rest queue into its request timeout. Set `MATHX_CONCURRENCY` to the server's
  real parallelism and keep `--max-tokens` low enough that one sample fits inside the
  server's per-request timeout, or your lanes will fill with timeout errors (honestly
  labelled, but wasted).

## 1. Recover and verify a formula — KL between two Gaussians

You're writing an ELBO and half-remember the closed form. Sign errors and swapped σ's are
the canonical fumble, and one model sample is exactly as trustworthy as your memory.

```bash
# 1. fan out and vote on the closed form
mathx solve "State the closed form of KL(N(mu1, s1^2) || N(mu2, s2^2)), the KL divergence between two univariate normal distributions. Answer with a single expression in mu1, mu2, s1, s2 only." \
  --k 16 --max-k 32 --out kl.json

# 2. read the vote
mathx show kl.json
```

**What you should see:** the winning cluster is
$\ln(s_2/s_1) + \frac{s_1^2 + (\mu_1-\mu_2)^2}{2 s_2^2} - \frac{1}{2}$.
The vote is over *meanings*, not strings — math-verify clusters algebraically equivalent
formulae. That is also why the problem statement demands "a single expression": if samples
wrap the answer in a restatement of the question ("KL(…) = …"), every notation for that
left-hand side becomes its own cluster and a real consensus reads as a fragmented vote. The
general rule is to pin the answer *form* in the problem statement — equations are fine
answers when the problem genuinely asks for an identity or relation ("state the recurrence…")
and the CAS compares them as equations. When a margin looks absurdly split, inspect the
clusters with `mathx show` before distrusting the answer.

```bash
# 3. promote the winner to a CHECKED claim — the oracle becomes the inner call
mathx check "For probability densities p = N(mu1, s1^2) and q = N(mu2, s2^2) with s1 > 0 and s2 > 0, the integral of p(x)*ln(p(x)/q(x)) over the real line equals ln(s2/s1) + (s1^2 + (mu1 - mu2)^2)/(2*s2^2) - 1/2." \
  --grade-k 8 --exec-timeout 120

# 4. inspect the evidence
mathx show <run-id> --script 0
```

**What you should see:** `status: supported`, and a checker script that either does the
Gaussian integral symbolically with sympy or tests random (μ, σ) draws numerically (mpmath
ships with sympy) — both count. Note the claim-writing discipline in step 3: the claim
restates the formula as a self-contained integral statement, definitions inside the claim.
That is what makes it mechanically checkable.

**Honesty note:** `supported` means the script's checks passed and 8 graders voted — it is
evidence, not proof. `INCONCLUSIVE` on the tir lane usually means the symbolic integral
timed out; raise `--exec-timeout` or read the grade lane alone.

## 2. Audit a belief — softmax: shift-invariant, scale-invariant?

Softmax is invariant to adding a constant to the logits. Is it invariant to *scaling* them?
It sounds symmetric — and the existence of temperature scaling says it isn't.

```bash
# 1. the true sibling
mathx check "For every real vector z of length n >= 2 and every real constant c, softmax(z + c*1)_i = softmax(z)_i for all i, where softmax(z)_i = exp(z_i) / sum_j exp(z_j)."
echo $status   # 0 = supported (fish; use $? in bash)

# 2. the false sibling — the belief under audit
mathx check "For every real vector z of length n >= 2 and every constant c > 0, softmax(c*z)_i = softmax(z)_i for all i, where softmax(z)_i = exp(z_i) / sum_j exp(z_j)."
mathx show <run-id> --script 0
```

**What you should see:** the first claim `supported` (exit 0); the second `refuted` (exit 1)
with a concrete counterexample in the script output — e.g. $z=(0,1)$, $c=2$ gives
$(0.269, 0.731)$ vs $(0.119, 0.881)$. Two adjacent-sounding claims, opposite verdicts,
evidence attached. If a weak grader lane votes TRUE while the script finds the
counterexample, the status is `conflict` (exit 2) — that disagreement being *surfaced
instead of averaged away* is why two verdict lanes exist.

```bash
# 3. the whole argument, as a claim ledger
mathx argue "Show that softmax is invariant under adding a constant to every logit, but not invariant under multiplying all logits by a positive constant, and explain the role of the temperature parameter." \
  --rounds 2
mathx show <ledger-id>

# 4. poke the ledger
mathx ledger challenge <ledger-id> c2 "what happens at c = 1?"
mathx ledger recheck   <ledger-id> c1 --grade-k 16
mathx show <ledger-id>          # badges refresh live from the job store
```

**What you should see:** an argument decomposed into self-contained claims, each with a
verdict badge and a job id you can audit (`mathx show <job-id>`). If the decomposer
over-claims — "softmax(c·z) ≠ softmax(z) for **all** c > 0" is false at c = 1 — the check
refutes it, the claim lands in the scratchpad, and a refinement round repairs the wording:
the loop working as designed. If it doesn't happen naturally, your `challenge` at c = 1
makes the same point deliberately.

A `conflict` badge is the workflow's finest moment, not a malfunction. In our live run, a
temperature-monotonicity claim came back *tir: fail, grade: true 2/2*. Reading the script
(`mathx show <job-id> --script 0`) settled it in one glance: the script's symbolic comments
derived the claim correctly, then its numeric loop tested the bare probability
$\sigma_T(z)_i$ instead of the claimed ratio $\sigma_T(z)_i/\sigma_T(z)_j$ — a buggy test,
true claim, graders right. `mathx ledger recheck <ledger> c6` then upgraded the badge to
supported on a fresh 2/2 grade vote (the earlier conflict verdict stays in the claim's
history). The conflict didn't tell you the answer; it told you exactly where to look — that
is the product.

## Backgrounding any of this

Every blocking call above has a job-store form: `mathx submit --check "<claim>"` returns a
job id immediately; poll `mathx status <id>` (exit 2 while running), render with
`mathx show <id>`, list history with `mathx jobs`.
