# BTC DCA — Regime-Aware Strategy

A weekly investing tool that tells you how to split your paycheck
between Bitcoin and everything else, based on where BTC sits in its
market cycle and what percentage of your portfolio it already occupies.

---

## Quick start (Mac)

1. Click the green **Code** button on this page → **Download ZIP**
2. Unzip the folder anywhere (Desktop is fine)
3. Double-click **RunBTCDCA.command**

That's it. The first time it runs, it installs everything it needs
automatically (takes about 30 seconds). Every time after that it
goes straight to the tool.

> **First-time Mac security prompt:** macOS may say the file "cannot
> be opened because it is from an unidentified developer." If you see
> this, right-click the file → **Open** → **Open** in the dialog.
> You only have to do this once.

**Requires Python 3.** If the script says Python isn't installed,
download it from [python.org/downloads](https://www.python.org/downloads/)
then double-click again.

---

## What it asks each week

When you run it, it asks three things:

1. **How much do you have to invest this round?** — whatever cash you
   have available this week or pay period (any amount)
2. **Your total BTC holdings** — in BTC, across all wallets/exchanges
3. **Value of your other investments** — stocks, ETFs, 401k, in dollars

The current Bitcoin price is fetched automatically — you just confirm it.

---

## What it tells you

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

One number to copy to your exchange. The rest goes to your other
investments as usual.

---

## The two numbers to set before your first run

Open `btc_dca.py` in any text editor and find these two lines near
the top:

```python
BTC_TARGET_PCT = 20.0   # target Bitcoin as % of your total portfolio
BASE_DEPOSIT   = 2000   # your total weekly investment across everything ($)
```

Change them to match your situation. Everything else is automatic.

---

## How it works (the idea)

Bitcoin goes through distinct bull and bear markets, roughly following
a 4-year cycle around each "halving." The tool detects which phase
you're in using a 20-week moving average and scales your Bitcoin
allocation accordingly:

- **Bear market:** price falling below its recent average — deploy
  more into BTC. This is when it's cheap.
- **Bull market:** price rising above its recent average — deploy
  less. Price is expensive; don't over-buy at the top.
- **Neutral:** transitioning — deploy a moderate amount.

The split between BTC and your other investments is driven by two
things equally: the regime signal above, and how close your portfolio
is to your target allocation. If you're well below 20% BTC, the tool
leans toward BTC. If you're above 40%, it pauses BTC buying for the
week.

**Why no limit orders?** Tested against 12 years of real data — limit
orders consistently underperformed just buying at market. With orders
filling only ~20% of weeks, too much cash sits idle. The regime timing
is what actually adds value.

---

## Backtest results (11.8 years of real BTC data)

```
                          Strategy    Flat DCA
  ────────────────────────────────────────────
  BTC accumulated           251.74      131.77
  BTC ratio vs flat DCA      1.91x       1.00x
  Final value         $15,760,129   $8,249,827
  Max drawdown               82.3%
```

*Flat DCA: same dollar amount into BTC every single week, no timing.*

The 1.91x ratio means the strategy accumulated 91% more BTC than
just buying a fixed amount every week — by buying more when price was
cheap and less when it was expensive.

---

## Advanced configuration

Everything configurable lives at the top of `btc_dca.py`. The two
you already set are the main ones. The rest have sensible defaults and
only need changing if you want to tune the strategy's aggressiveness:

| Setting | Default | What it does |
|---|---|---|
| `BTC_TARGET_PCT` | 20.0 | Target % of portfolio in BTC |
| `BASE_DEPOSIT` | 2000 | Total weekly investment ($) |
| `BTC_FLOOR_PCT` | 10.0 | If BTC drops below this %, force aggressive buying |
| `BTC_CEILING_PCT` | 40.0 | If BTC rises above this %, skip BTC buy this week |
| `BEAR_MULT_MIN/MAX` | 1.0–4.0 | Range of bear-market multipliers for optimizer to search |
| `NEUT_MULT_MIN/MAX` | 0.5–1.5 | Range of neutral multipliers |
| `BULL_MULT_MIN/MAX` | 0.25–1.0 | Range of bull multipliers |
| `AVG_MULT_MAX` | 2.0 | Max average weekly spend vs base — set this to how large a cash reserve you can maintain |
| `REGIME_MA_WEEKS` | 20 | Weeks of history for bull/bear detection |

The optimizer runs automatically every 30 days and finds the best
specific values within these ranges using real BTC price history.

---

## Files

| File | What it is |
|---|---|
| `RunBTCDCA.command` | Double-click this to run (Mac) |
| `btc_dca.py` | The program itself |
| `requirements.txt` | Python dependencies (installed automatically) |
| `.btc_params.json` | Optimizer results — auto-generated, safe to delete |
| `.btc_cache.json` | Cached price history — auto-generated, safe to delete |
| `.btc_memory.json` | Your weekly state — auto-generated, safe to delete |

The dot-files (`.btc_*`) are hidden by default on Mac. They're
created automatically and regenerated if deleted.

---

## Important caveats

**This is not financial advice.** This tool helps you implement a
systematic strategy — it doesn't guarantee returns or protect against
loss.

**The 82% max drawdown is real.** During the 2022 bear market,
Bitcoin fell ~75%. The strategy buys more during crashes, which means
your portfolio value can drop sharply before recovering. Only use
capital you're comfortable holding through multi-year downturns.

**Past performance doesn't guarantee future results.** The backtest
covers one specific period of Bitcoin's history. Future cycles may
behave differently.

**You place all trades manually.** The tool tells you what to buy —
it never accesses your exchange or moves money automatically.