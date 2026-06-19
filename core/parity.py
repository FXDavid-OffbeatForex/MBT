"""
Signal parity — the mechanical "does it match the strategy" check.

Given two signal sets (same MBT CSV format) it diffs them bar-by-bar and reports
the first place they diverge. Typical use: the EA's logged signals (candidate)
vs a trusted reference — the source indicator's signals, or a strategy
prototype's exported signals. A match means the EA fires the same trades as the
reference; the first divergence pinpoints where the port drifted.

This is deterministic (a diff), not a judgement — unlike reading a PRD and
eyeballing the code. It is strategy-agnostic: MBT just compares two signal files,
so the reference can come from anywhere (no engine dependency).
"""

from .signals import load_signals


def _key(sig):
    return sig.time


def signal_parity(reference: str, candidate: str = None,
                  price_tol: float = 0.0, max_examples: int = 10) -> dict:
    """
    reference : path to the trusted reference signal CSV.
    candidate : path to the candidate CSV; defaults to the configured signal_file.
    price_tol : max abs difference allowed on entry/sl/tp to still count as a match.
    """
    try:
        ref = load_signals(reference)
    except Exception as e:
        return {"error": f"could not load reference '{reference}': {e}"}
    try:
        cand = load_signals(candidate) if candidate else load_signals()
    except Exception as e:
        return {"error": f"could not load candidate '{candidate or '(config signal_file)'}': {e}"}

    ref_by  = {_key(s): s for s in ref}
    cand_by = {_key(s): s for s in cand}
    # Matching is keyed by bar time (one signal per bar). If a file has multiple
    # signals on the same bar the dict keeps the last — surface that rather than
    # diff silently against a collapsed set.
    ref_dups  = len(ref)  - len(ref_by)
    cand_dups = len(cand) - len(cand_by)
    times   = sorted(set(ref_by) | set(cand_by))

    matched = mismatched = 0
    only_ref = only_cand = 0
    diffs = []            # chronological list of divergences

    def near(a, b):
        try:
            return abs(a - b) <= price_tol
        except TypeError:
            return False          # a malformed (None/non-numeric) price -> mismatch, not a crash

    for t in times:
        r = ref_by.get(t)
        c = cand_by.get(t)
        if r and not c:
            only_ref += 1
            diffs.append({"time": t.strftime("%Y-%m-%d %H:%M"),
                          "issue": "in reference, missing from candidate",
                          "reference": r.direction})
        elif c and not r:
            only_cand += 1
            diffs.append({"time": t.strftime("%Y-%m-%d %H:%M"),
                          "issue": "in candidate, missing from reference",
                          "candidate": c.direction})
        else:
            same = (r.direction.upper() == c.direction.upper()
                    and near(r.entry, c.entry)
                    and near(r.sl, c.sl) and near(r.tp, c.tp))
            if same:
                matched += 1
            else:
                mismatched += 1
                diffs.append({
                    "time": t.strftime("%Y-%m-%d %H:%M"),
                    "issue": "same bar, different signal",
                    "reference": {"dir": r.direction, "entry": r.entry, "sl": r.sl, "tp": r.tp},
                    "candidate": {"dir": c.direction, "entry": c.entry, "sl": c.sl, "tp": c.tp},
                })

    if len(ref) == 0 and len(cand) == 0:
        verdict = "no_data"           # both empty — NOT a real match
    elif mismatched == 0 and only_ref == 0 and only_cand == 0:
        verdict = "identical"
    else:
        verdict = "diverges"
    out = {
        "reference": reference,
        "candidate": candidate or "(config signal_file)",
        "reference_count": len(ref),
        "candidate_count": len(cand),
        "matched": matched,
        "mismatched": mismatched,
        "only_in_reference": only_ref,
        "only_in_candidate": only_cand,
        "price_tol": price_tol,
        "verdict": verdict,
        "first_divergence": diffs[0] if diffs else None,
        "divergences": diffs[:max_examples],
    }
    if ref_dups or cand_dups:
        out["warning"] = (f"multiple signals share a bar time "
                          f"(reference: {ref_dups}, candidate: {cand_dups}) — these "
                          f"are compared by last-per-bar; counts are over unique bars.")
    return out
