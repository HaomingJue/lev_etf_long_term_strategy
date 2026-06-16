"""
tax_engine.py — combined federal + Ontario personal income tax, brackets
versioned by year.

The bracket *data* lives in tax_brackets.json (year -> table); the *calculation*
lives here. Splitting them lets a backtest use correct per-year brackets while
staying fully reproducible and offline — unlike fetching rates live at runtime.

For a year with no entry in the JSON, the NEAREST available year is used, so a
file seeded with a single year reproduces the old hardcoded behaviour exactly.
Populate more years (from CRA / Ontario Ministry of Finance) to make the backtest
bracket-accurate per year — see update_tax_brackets.py to copy/index a year.

Public API:
    brackets_for(year)                         -> dict (the year's table)
    total_tax(taxable, year)                   -> combined fed+ON+surtax, net of BPAs
    tax_on_investment(salary, gains, interest, year)
                                               -> incremental tax on investment
                                                  income stacked on top of salary
    cg_inclusion(year)                         -> capital-gains inclusion rate

Model notes (carried over from the original inline model):
  - Capital gains: `gains` passed in are already inclusion-applied by the caller;
    Canada has no short/long-term distinction.
  - Interest (e.g. T-bill cash yield) is 100% taxable as income.
  - Federal/Ontario basic personal amounts are applied as flat credits (no
    high-income BPA phase-out modelled). Ontario surtax is applied on basic ON
    tax. Ignores CPP/EI, Ontario Health Premium, and other credits.
  - Registered accounts (TFSA/RRSP) are untaxed — don't use this for those.
"""
import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "tax_brackets.json"


def _norm_brackets(brackets):
    """[[upper, rate], ...] with 'inf' allowed -> [(float_upper, rate), ...]."""
    return [(float("inf") if u in ("inf", "Infinity") else float(u), float(r))
            for u, r in brackets]


@lru_cache(maxsize=1)
def _load() -> dict:
    raw = json.loads(_DATA.read_text(encoding="utf-8"))
    table = {}
    for key, v in raw.items():
        if key.startswith("_"):          # _about / _schema metadata
            continue
        table[int(key)] = {
            "fed_brackets": _norm_brackets(v["fed_brackets"]),
            "on_brackets":  _norm_brackets(v["on_brackets"]),
            "fed_bpa":      float(v["fed_bpa"]),
            "on_bpa":       float(v["on_bpa"]),
            "on_surtax_t1": float(v["on_surtax_t1"]),
            "on_surtax_t2": float(v["on_surtax_t2"]),
            "cg_inclusion": float(v.get("cg_inclusion", 0.50)),
        }
    if not table:
        raise ValueError(f"{_DATA} contains no bracket years")
    return table


def available_years() -> list:
    return sorted(_load())


def brackets_for(year: int) -> dict:
    """The bracket table for `year`, or the nearest populated year if absent."""
    table = _load()
    if year in table:
        return table[year]
    return table[min(table, key=lambda y: (abs(y - year), y))]


def _bracket_tax(income: float, brackets) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        if income <= lower:
            break
        tax += (min(income, upper) - lower) * rate
        lower = upper
    return tax


def total_tax(taxable: float, year: int) -> float:
    """Combined federal + Ontario tax (incl. Ontario surtax), net of the basic
    personal-amount credits, for `year`'s brackets."""
    if taxable <= 0:
        return 0.0
    t   = brackets_for(year)
    fed = max(_bracket_tax(taxable, t["fed_brackets"])
              - 0.15 * min(t["fed_bpa"], taxable), 0.0)
    on  = max(_bracket_tax(taxable, t["on_brackets"])
              - 0.0505 * min(t["on_bpa"], taxable), 0.0)
    surtax = (0.20 * max(on - t["on_surtax_t1"], 0.0)
              + 0.36 * max(on - t["on_surtax_t2"], 0.0))
    return fed + on + surtax


def tax_on_investment(salary: float, taxable_gains: float,
                      interest: float, year: int) -> float:
    """Incremental tax from investment income stacked on top of `salary`, using
    `year`'s brackets. `taxable_gains` must already be inclusion-applied."""
    extra = max(taxable_gains, 0.0) + max(interest, 0.0)
    if extra <= 0:
        return 0.0
    return total_tax(salary + extra, year) - total_tax(salary, year)


def cg_inclusion(year: int) -> float:
    return brackets_for(year)["cg_inclusion"]


if __name__ == "__main__":
    yrs = available_years()
    print(f"tax_brackets.json: {len(yrs)} year(s) populated: {yrs}")
    for sal in (50_000, 100_000, 150_000):
        gain = 100_000 * cg_inclusion(yrs[-1])
        t = tax_on_investment(sal, gain, 0.0, yrs[-1])
        print(f"  {yrs[-1]}  salary ${sal:>7,}  +$100k gain (50% incl) "
              f"-> incremental tax ${t:,.0f}")
