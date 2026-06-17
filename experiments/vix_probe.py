"""
EXPERIMENT (sandbox) — does VIX discriminate the strategy's bad years?

Before building VIX into the grid (#3), check the prerequisite: do the strategy's
KNOWN losing/whipsaw years actually show elevated VIX? If 2015-16 (the worst OOS
stretch) had ordinary VIX, then a VIX gate cannot fix them and #3's payoff is
limited to 2022-style stress. This is a 30-second go/no-go probe.

QQQ Balanced (DD-Capped) walk-forward yearly returns are hardcoded from
results/walkforward/QQQ_walkforward_2015-2026_gridv3_selmaxdd50_yearly.csv.
"""
import numpy as np
import pandas as pd
import yfinance as yf

BAL = {2015:-15.1, 2016:-10.43, 2017:118.06, 2018:20.61, 2019:25.14,
       2020:108.15, 2021:82.98, 2022:-22.6, 2023:75.21, 2024:55.99,
       2025:29.43, 2026:16.31}

print("Downloading ^VIX …")
vraw = yf.download("^VIX", start="1990-01-01", end="2026-06-12",
                   auto_adjust=True, progress=False)
vix = vraw["Close"].squeeze().dropna()
vix.index = pd.to_datetime(vix.index)
print(f"  ^VIX coverage: {vix.index[0].date()} -> {vix.index[-1].date()} "
      f"({len(vix):,} days)\n")

rows = []
for yr in range(2015, 2027):
    v = vix[vix.index.year == yr]
    if v.empty:
        continue
    rows.append({
        "year": yr,
        "Balanced ret%": f"{BAL.get(yr, float('nan')):+6.1f}",
        "VIX mean": f"{v.mean():5.1f}",
        "VIX median": f"{v.median():5.1f}",
        "VIX max": f"{v.max():5.1f}",
        "days>30": int((v > 30).sum()),
        "days>35": int((v > 35).sum()),
        "loser?": "  LOSS" if BAL.get(yr, 0) < 0 else "",
    })
print(pd.DataFrame(rows).to_string(index=False))

print("\nMeta AI's proposed gate = stay in cash when VIX > 35.")
for yr in (2015, 2016, 2022):
    v = vix[vix.index.year == yr]
    print(f"  {yr} (Balanced {BAL[yr]:+.1f}%): VIX>35 on {int((v>35).sum())} days, "
          f"max VIX {v.max():.1f}")
