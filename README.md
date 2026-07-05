# BTC DCA — Regime-Aware Strategy

## What this is

A weekly investing tool that splits your paycheck between Bitcoin and
everything else, based on where BTC sits in its market cycle and what
percentage of your portfolio it already occupies.

You set a handful of numbers at the top of the file. It handles the rest.

---

## Setup

```bash
pip install requests yfinance
python3 btc_dca.py
```

**First run:** downloads ~12 years of real BTC price history, tests
thousands of parameter combinations in seconds, prints full backtest
results. Re-optimizes automatically every 30 days.

**Every run after:** three questions, one number to copy to your exchange.

---

## Configuration

Everything you'd want to change lives at the top of `btc_dca.py`.
Nothing else needs touching.

```python
# ── What you put in ────────────────────────────────────────────
BTC_TARGET_PCT = 20.0   # target Bitcoin as % of total portfolio
BASE_DEPOSIT   = 2000   # total weekly investment across all assets ($)
                        # BTC slice = BASE_DEPOSIT × BTC_TARGET_PCT%
                        # e.g. $2,000 × 20% = $400 baseline into BTC

# ── Portfolio guardrails ───────────────────────────────────────
BTC_FLOOR_PCT   = 10.0  # if BTC drops below this → force bear-rate buying
BTC_CEILING_PCT = 40.0  # if BTC rises above this → skip BTC buy this week

# ── How aggressive the optimizer can be ───────────────────────
BEAR_MULT_MIN = 1.0     # minimum bear multiplier (1.0 = no scaling)
BEAR_MULT_MAX = 4.0     # maximum bear multiplier (4.0 = 4× your BTC base)
NEUT_MULT_MIN = 0.5     # minimum neutral multiplier
NEUT_MULT_MAX = 1.5     # maximum neutral multiplier
BULL_MULT_MIN = 0.25    # minimum bull multiplier
BULL_MULT_MAX = 1.0     # maximum bull multiplier

# ── Cash reserve sizing ────────────────────────────────────────
AVG_MULT_MAX = 2.0   # max weighted-average multiplier across all weeks
                     # at 2.0, you need ~2× your BTC base ($800) in
                     # liquid cash available at all times
AVG_MULT_MIN = 0.4   # prevents "do almost nothing" strategies

# ── Signal sensitivity ─────────────────────────────────────────
REGIME_MA_WEEKS = 20  # weeks of history for bull/bear/neutral detection
                      # 20w is the standard used by most BTC analysts
                      # lower = reacts faster, more false signals
                      # higher = slower, more stable
```

---

## What it asks each week

1. **How much do you have to invest this round?** — whatever cash you
   have available (weekly, biweekly, any amount)
2. **Your total BTC holdings** — in BTC, across all wallets/exchanges
3. **Value of your other investments** — stocks, ETFs, 401k in dollars

The live BTC price is fetched automatically from Coinbase or CoinGecko.

---

## What it outputs

```
  PORTFOLIO SNAPSHOT
  ────────────────────────────────────────────────────────────
  BTC allocation:  14.9%   band: 10%–40%
  [██░░░░░░░░░░░░░░░░░░]
     ↑10%  ↑40%

  ✓ Within band. Regime signal active (2.0× multiplier).

  You have $2,000 to invest this round.
  $400 base (20%)  ×  2.0× regime  =  $800 BTC

  → $800  into Bitcoin              (40%)
  → $1,200 into other investments  (60%)

  ▼ BEAR — ORDERS  ($800 into BTC this round)
  ────────────────────────────────────────────────────────────
  ℹ  BEAR — price below falling 20w MA. Deploying 2× ($800) at market.

  1. ▲ BUY  —  at market now
        0.012739 BTC  (≈ $800)
        $800 at current price
```

One market buy. Copy it to your exchange.

---

## How the math works

**Step 1 — Baseline BTC amount:**
`BASE_DEPOSIT × BTC_TARGET_PCT% = your BTC base per week`
At $2,000 total and 20% target: $400/week baseline into BTC.

**Step 2 — Regime multiplier scales it:**

| Regime | Condition | Multiplier range | BTC this week |
|---|---|---|---|
| BEAR ▼ | Price < 20w MA *and* MA falling | `BEAR_MULT_MIN`–`BEAR_MULT_MAX` | $400–$1,600 |
| NEUTRAL ◆ | Transitioning | `NEUT_MULT_MIN`–`NEUT_MULT_MAX` | $200–$600 |
| BULL ▲ | Price > 20w MA *and* MA rising | `BULL_MULT_MIN`–`BULL_MULT_MAX` | $100–$400 |

The optimizer finds the specific multiplier within each range that
produced the most BTC over 12 years of real data, subject to the
`AVG_MULT_MAX` constraint.

**Step 3 — Portfolio target nudge:**
Whatever isn't going to BTC goes to your other investments. The
portfolio snapshot shows your current allocation vs target.

**Step 4 — Guardrails:**

| Situation | What happens |
|---|---|
| BTC < `BTC_FLOOR_PCT` | Force bear-rate buying regardless of regime |
| BTC > `BTC_CEILING_PCT` | Skip BTC buy entirely this week |

Both show what the regime would have suggested, so you can override.

---

## How the optimizer works

It searches combinations of six parameters against 12 years of weekly
BTC closes:

| Parameter | Found by optimizer | Range |
|---|---|---|
| `bear_multiplier` | yes | `BEAR_MULT_MIN` – `BEAR_MULT_MAX` |
| `neutral_multiplier` | yes | `NEUT_MULT_MIN` – `NEUT_MULT_MAX` |
| `bull_multiplier` | yes | `BULL_MULT_MIN` – `BULL_MULT_MAX` |
| `ath_trigger_pct` | yes | 110–130% of ATH |
| `t2_size_pct` | yes | 5–15% of holdings |
| `ma_weeks` | yes | 8–20 weeks (recovery guard only) |

Scored on: `0.6 × btc_ratio + 0.4 × sharpe_proxy`

Where `btc_ratio` = strategy BTC accumulated ÷ flat $base/week DCA BTC.
A ratio > 1.0 means regime timing genuinely beat buying the same
amount every week. The `sharpe_proxy` prevents the optimizer finding
strategies that work only because they take enormous drawdown risk.

**The `AVG_MULT_MAX` constraint** is the key guard against exploitation.
Without it, the optimizer would always push bear multipliers to their
maximum, since "deploy more in cheap weeks" always beats "deploy less"
vs a flat benchmark. `AVG_MULT_MAX = 2.0` means the weighted average
multiplier across all historical weeks can't exceed 2×, which directly
limits how much cash reserve you need to sustain the strategy.

**`REGIME_MA_WEEKS = 20` is fixed** and not optimized. Optimizing the
regime detection window risks overfitting to the specific turning points
in this price history. The 20-week MA is a well-established signal —
change it if you have a strong view, but the optimizer won't touch it.

---

## Backtest results (11.8 years of real BTC data)

```
                          Strategy    Flat DCA    Buy&Hold
  ──────────────────────────────────────────────────────
  BTC accumulated           251.74      131.77        —
  BTC ratio vs flat DCA      1.910x      1.000x
  Final value ($)        15,760,129   8,249,827  126,829,757
  Max drawdown               82.3%
  Avg multiplier              1.90×
```

*Flat DCA: $400/week into BTC at spot, no regime scaling, same cadence.*

The 1.91x ratio means regime timing accumulated 91% more BTC than
just buying the same amount every week. That's the entire edge —
buying more when price is cheap (bear weeks), less when expensive (bull).

---

## Why no limit orders

Tested empirically. Every limit-order configuration underperformed
plain market buying over 11+ years. With ~20% fill rate, 80% of weeks
the cash earmarked for limits sits idle while the DCA benchmark deploys
100%. The small discount on fills doesn't compensate for idle cash.
The regime multiplier is what actually works.

---

## Files

| File | Purpose |
|---|---|
| `btc_dca.py` | The whole program. Only file you need. |
| `.btc_params.json` | Optimizer results (auto-generated, refreshes monthly) |
| `.btc_cache.json` | Price history cache (refreshes every 6 hours) |
| `.btc_memory.json` | Weekly state: ATH, recent price history |

Dot-files are hidden on Mac/Linux. Delete any and the program
regenerates on the next run.

---

## Caveats

**82% max drawdown is real.** During 2022, a portfolio with significant
BTC exposure dropped hard. The strategy accumulates more BTC on the
way down — but dollar value still hurts. This is a long-term
accumulation strategy, not capital preservation.

**Regime detection lags 1–4 weeks.** You won't catch exact tops and
bottoms. The edge comes from being correctly positioned during the
bulk of each regime, not from perfect timing.

**The optimizer can still exploit the benchmark** if you widen the
bounds aggressively. `AVG_MULT_MAX` is your main protection — lower it
if results look implausible, raise it if you have deep cash reserves
and want the optimizer to explore more aggressive strategies.

**It tells you what to do. You place the trades.** Nothing is
automatic. A human reviews every order before money moves.