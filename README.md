# BTC DCA — Regime-Aware Strategy

## What this is

A weekly investing tool that looks at your full financial picture —
how much you have to invest, what your portfolio looks like, and where
Bitcoin currently sits in its market cycle — and tells you exactly how
to split your money and what orders to place.

You tell it how much cash you have this week. It tells you:
- How much to put into Bitcoin
- How much to put into your other investments (stocks, ETFs, 401k)
- Exactly which BTC orders to place and at what prices

---

## Setup

```bash
pip install requests yfinance
python3 btc_dca.py
```

Open `btc_dca.py` and set the two values near the top before your
first run:

```python
BTC_TARGET_PCT = 20.0   # target Bitcoin as % of your total portfolio
BASE_DEPOSIT   = 725    # your comfortable average weekly investment ($)
```

**First run:** downloads ~12 years of BTC history, tests ~470,000
parameter combinations across bull, bear, and neutral regimes. Takes
5–10 minutes. Repeats automatically every 30 days.

**Every run after:** loads settings, fetches live price, asks three
questions, prints your split and orders.

---

## What it asks each week

1. **How much do you have to invest this round?** — your available
   cash, whatever it is this week or this pay period
2. **Your total BTC holdings** — in BTC, across all wallets/exchanges
3. **Value of your other investments** — stocks, ETFs, 401k in dollars
4. **Did last week's limit orders fill?** — only if you had limits out

---

## What it outputs

**First: your split**
```
  PORTFOLIO SNAPSHOT
  ──────────────────────────────────────────────────────
  BTC allocation:  14.2%  [target: 20%]
  [██░░░░░░░░░░░░░░░░░░]
  ↑ 5.8% under target — nudging more toward BTC this week

  You have $1,500 to invest this round.

  → Put $950 into Bitcoin   (63% of this round)
  → Put $550 into your other investments   (37% of this round)
```

**Then: your BTC orders**
```
  ▼ BEAR — ORDERS  (deposit this round: $950)
  ──────────────────────────────────────────────────────
  ℹ  BEAR market detected — deploying 2× base ($950).
     Buying full amount at market now; limits set for extra accumulation.

  1. ▲ BUY  —  at market now
        0.015873 BTC  (≈ $950)
        Full bear deposit at market — price is cheap

  2. ▲ BUY  —  limit at $57,930
        0.016397 BTC  (≈ $950)
        3% under spot  (+$30 above nearest $1k level)

  3. ▲ BUY  —  limit at $51,930
        0.018294 BTC  (≈ $950)
        9% under spot  (+$30 above nearest $1k level)
```

---

## How the split is calculated

The tool balances two things:

**Regime signal** (primary): Is Bitcoin in a bear, neutral, or bull
market? Bear = put more into BTC this week. Bull = put a bit less.
This is determined by the 20-week moving average — no prediction, just
detecting where we are now.

**Portfolio target** (secondary nudge): Are you above or below your
20% BTC target? If you're 10% under, the tool nudges the BTC slice
a bit higher. If you're over target, it nudges toward stocks. The nudge
is capped at ±15% so it never overrides the regime signal — it just
tilts within the regime's range.

The remaining cash after the BTC slice is your stocks/ETFs allocation
for the week. The tool doesn't pick which stocks — just gives you the
dollar amount to put into whatever your normal index fund or 401k
contribution is.

---

## How regime detection works

Uses Bitcoin's 20-week moving average with slope confirmation:

| Condition | Regime | Effect |
|---|---|---|
| Price above MA *and* MA rising | **BULL ▲** | Smaller BTC slice, shallow buy limits (0.5–2% under spot) |
| Price below MA *and* MA falling | **BEAR ▼** | Larger BTC slice, deeper limits (3–12%), full amount at market |
| Anything else | **NEUTRAL ◆** | Moderate BTC slice, limits for dips |

This is detection with a 1–4 week lag, not prediction. That's fine —
the goal is to be positioned correctly during the bulk of each regime.

---

## Backtest results (11.8 years of real BTC data)

```
                          Strategy   Plain DCA    Buy&Hold
  ──────────────────────────────────────────────────────
  ROI                      5778.5%    3408.5%    28263.8%
  BTC accumulated           400.18     238.84        n/a
  BTC ratio vs DCA            1.68x      1.00x
  Max drawdown                82.4%

  Regime breakdown:
  ✓ Bear markets    3.0x DCA  (+200% BTC accumulated)
  ✓ Neutral         1.25x DCA  (+25% BTC accumulated)
  ✓ Bull markets    1.0x DCA   (matches DCA, stays invested)
```

The strategy's edge is in bear markets — deploying more capital when
price is cheap accumulates significantly more BTC than plain weekly
buying. In bull markets it stays close to DCA performance rather than
over-paying.

---

## What the parameters mean

| Parameter | What it does |
|---|---|
| `BTC_TARGET_PCT` | Your target Bitcoin allocation as % of total portfolio |
| `BASE_DEPOSIT` | Your average comfortable weekly investment. Regime multipliers scale from this. |
| `bear_multiplier` | How aggressively to deploy in a bear (e.g. 2.0 = double your base into BTC) |
| `neutral_multiplier` | Scaling in neutral conditions (typically 1.0–1.25×) |
| `bull_multiplier` | Scaling in a bull run (typically 0.5–0.75× — price is expensive) |
| `bear_bl1_pct / bear_bl2_pct` | Bear market limit depths — deeper since meaningful dips are common |
| `bull_bl1_pct / bull_bl2_pct` | Bull market limit depths — shallower since dips are small |
| `bull_carry_pct` | % of deposit to buy at market in bull/neutral (rest waits for limits) |
| `ath_trigger_pct` | Price must exceed this % of all-time high to trigger the ATH sell |
| `t2_size_pct` | % of total BTC holdings to sell when ATH trigger fires (the only sell) |
| `ma_weeks` | Recovery guard: after a crash buy, no ATH sells until price is back above this MA |
| `gw_offset` | Dollar nudge past round numbers where other traders cluster orders |

---

## Files

| File | Purpose |
|---|---|
| `btc_dca.py` | The whole program. Only file you need. |
| `.btc_params.json` | Optimized parameters (auto-generated, refreshes monthly) |
| `.btc_cache.json` | Price history cache (refreshes every 6 hours) |
| `.btc_memory.json` | Weekly state: ATH, price history, last fill status |

Dot-files are hidden by default on Mac/Linux. Delete any and the
program regenerates on next run.

---

## Honest caveats

**The 82% max drawdown is real.** During the 2022 bear, your portfolio
would have been down ~82% from its peak. The strategy helps you
accumulate more BTC on the way down — but it doesn't protect your
dollar value. This is a long-term Bitcoin accumulation strategy, not
a capital-preservation strategy.

**Regime detection lags by 1–4 weeks.** You won't be perfectly
positioned at every turn. The edge comes from being roughly right
during the bulk of each regime, not from catching exact tops and
bottoms.

**It tells you what to do. You place the trades.** Orders are never
placed automatically. That's intentional — a human reviews every order
before money moves.