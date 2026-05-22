"""
Token and cost model for figures.

All pricing and per-call token assumptions live in ONE place so that:
  1. Numbers cannot drift between figures and tables.
  2. The assumptions are visible and easy to audit.
  3. Updating a price changes every figure on rebuild.

The numbers below are public list prices as of Q1 2026. Per-call token
estimates are coarse averages from spot-checks of the result JSONs.
They are intended for *relative* comparisons across methods on the
same benchmark, not for absolute budgeting.

USD-per-million-token rates are stored under PRICES[<model_key>] as
{"in": <$/Mtok>, "out": <$/Mtok>}.
"""

# ── 1. List prices (USD per million tokens) ──────────────────────────────────
# Public rate cards. If a number is wrong, fix it here and rebuild.
PRICES = {
    # Anthropic
    "sonnet-4.6":  {"in":  3.00, "out": 15.00},
    "haiku-4.5":   {"in":  0.80, "out":  4.00},
    "opus-4.6":    {"in": 15.00, "out": 75.00},
    # Google
    "gemini-3.1-pro":        {"in":  1.25, "out": 10.00},
    "gemini-3.1-flash-lite": {"in":  0.10, "out":  0.40},
    # OpenAI
    "gpt-5.4":       {"in":  2.00, "out":  8.00},
    "gpt-5.4-mini":  {"in":  0.40, "out":  1.60},
    "gpt-5.5-pro":   {"in": 15.00, "out": 60.00},  # extended-thinking tier
}

# ── 2. Per-call token profile (input/output tokens per single LLM call) ──────
# These are averages across a typical PVD round on GPQA. The same numbers
# are used for HLE (questions are similar length on average).
#
# PVD anatomy of one round:
#   - 1 verifier call: sees prover's last statement + brief history → emits
#     a verdict block + (if Challenge) a probe.
#   - 1 prover call: sees the running transcript → emits an updated answer
#     and reasoning.
# Initial prover call has no transcript so its input is shorter.

TOKENS = {
    # First prover statement (no transcript yet, just the question)
    "prover_initial":  {"in":  600, "out": 800},
    # Verifier turn (sees transcript)
    "verifier_turn":   {"in": 1500, "out": 500},
    # Prover follow-up (sees transcript including verifier's probe)
    "prover_followup": {"in": 1800, "out": 800},
    # A single SC sample (just question → answer with CoT)
    "sc_sample":       {"in":  300, "out": 800},
    # USC selector pass (sees k candidate answers)
    "usc_select":      {"in": 2500, "out": 200},
    # One debate agent turn (sees other agents' previous answers)
    "debate_turn":     {"in": 1200, "out": 700},
    # Direct/single-call baseline
    "direct":          {"in":  300, "out": 800},
}


# ── 3. Helpers ───────────────────────────────────────────────────────────────

def cost_for(model_key: str, tokens_in: float, tokens_out: float) -> float:
    """Compute USD cost for a single call given a model and token counts."""
    p = PRICES[model_key]
    return (tokens_in * p["in"] + tokens_out * p["out"]) / 1_000_000


def tokens_and_cost_pvd(prover: str, verifier: str,
                        avg_rounds: float, avg_attempts: float = 1.0) -> dict:
    """
    PVD (single-shot or retry) per-question token & cost estimate.

    Convention: for retry runs, `avg_rounds` is the *total* rounds across
    all attempts in a question (i.e. summed over attempts), not per-attempt.
    This matches how it is computed in pvd_stats() from the `total_rounds`
    field of retry JSONs.

    Per question:
      - avg_attempts × prover_initial calls (one initial per attempt)
      - avg_rounds   × verifier_turn calls
      - (avg_rounds - avg_attempts) × prover_followup calls

    `avg_rounds` counts verifier turns. Each attempt begins with an initial
    prover call and ends on a verifier verdict, so there is one fewer prover
    follow-up than verifier turn per attempt.

    Returns dict with keys: tokens_in, tokens_out, tokens_total, cost_usd, calls.
    """
    prover_in_initial  = TOKENS["prover_initial"]["in"]
    prover_out_initial = TOKENS["prover_initial"]["out"]
    prover_in_follow   = TOKENS["prover_followup"]["in"]
    prover_out_follow  = TOKENS["prover_followup"]["out"]
    verif_in           = TOKENS["verifier_turn"]["in"]
    verif_out          = TOKENS["verifier_turn"]["out"]

    followup_calls = max(0.0, avg_rounds - avg_attempts)

    prover_in_total  = avg_attempts * prover_in_initial  + followup_calls * prover_in_follow
    prover_out_total = avg_attempts * prover_out_initial + followup_calls * prover_out_follow
    verif_in_total   = avg_rounds * verif_in
    verif_out_total  = avg_rounds * verif_out

    cost = (cost_for(prover,   prover_in_total, prover_out_total) +
            cost_for(verifier, verif_in_total,  verif_out_total))

    tokens_in    = prover_in_total + verif_in_total
    tokens_out   = prover_out_total + verif_out_total
    calls = avg_attempts + avg_rounds + followup_calls

    return dict(
        tokens_in=tokens_in, tokens_out=tokens_out,
        tokens_total=tokens_in + tokens_out,
        cost_usd=cost,
        calls=calls,
    )


def tokens_and_cost_direct(model: str) -> dict:
    t = TOKENS["direct"]
    return dict(
        tokens_in=t["in"], tokens_out=t["out"],
        tokens_total=t["in"] + t["out"],
        cost_usd=cost_for(model, t["in"], t["out"]),
        calls=1.0,
    )


def tokens_and_cost_sc(model: str, k: int, include_usc: bool = False,
                       usc_model: str | None = None) -> dict:
    sc = TOKENS["sc_sample"]
    tin_sc  = k * sc["in"]
    tout_sc = k * sc["out"]
    cost = cost_for(model, tin_sc, tout_sc)
    calls = float(k)
    tin, tout = tin_sc, tout_sc
    if include_usc:
        u = TOKENS["usc_select"]
        um = usc_model or model
        cost += cost_for(um, u["in"], u["out"])
        tin  += u["in"]
        tout += u["out"]
        calls += 1
    return dict(
        tokens_in=tin, tokens_out=tout, tokens_total=tin + tout,
        cost_usd=cost, calls=calls,
    )


def tokens_and_cost_debate(model: str, n_agents: int, n_rounds: int) -> dict:
    t = TOKENS["debate_turn"]
    calls = n_agents * (1 + n_rounds)
    tin  = calls * t["in"]
    tout = calls * t["out"]
    return dict(
        tokens_in=tin, tokens_out=tout, tokens_total=tin + tout,
        cost_usd=cost_for(model, tin, tout),
        calls=float(calls),
    )
