# Bitcoin Weekly DCA Strategy

## The core idea (read this first)

Buying Bitcoin on a fixed schedule ("dollar-cost averaging") is simple and works well over time. This tool does something slightly smarter without requiring you to watch the market: instead of buying at whatever price happens to be when you get paid, it places **limit orders** a bit below the current price, so you only buy when the market dips toward you. It does the same thing in reverse for selling small amounts on the way up.

The goal isn't to "beat the market" with predictions. It's to **end up holding more Bitcoin per dollar spent** than you would with plain weekly buying — by taking advantage of Bitcoin's normal week-to-week price swings instead of ignoring them.

There are two parts:

1. **The optimizer** — looks at years of real Bitcoin price history and tests thousands of combinations of buy/sell settings to find which ones would have worked best in the past. You run this occasionally (e.g. every few months), not every week.
2. **The weekly calculator** — takes those settings and tells you exactly what orders to place this week, in plain language. This is the one you (or someone with zero technical background) runs regularly.

---

## Files in this project


| File                 | What it does                                                                                               |
| -------------------- | ---------------------------------------------------------------------------------------------------------- |
| `config.py`          | All the adjustable settings and ranges in one place. You rarely need to open this.                         |
| `strategy.py`        | The actual buy/sell rules — how prices and order sizes get calculated.                                     |
| `backtest.py`        | Tests the strategy against real historical Bitcoin prices to see how it would have performed.              |
| `main.py`            | The full command-line tool — runs the optimizer and the detailed order calculator. For the technical user. |
| `weekly.py`          | **The simple version.** Double-click it, answer two questions, get plain-English instructions. For anyone. |
| `params.json`        | The strategy settings the optimizer found (created automatically — don't edit by hand).                    |
| `weekly_memory.json` | `weekly.py` saves your last known state here automatically, so you don't have to track anything yourself.  |


---

## What the variables mean

Note: `weekly.py` fetches the current Bitcoin price automatically and lets the person choose weekly or biweekly each time they run it — they never type a price unless the automatic check fails, and the moving-average window quietly adjusts itself to match whichever cadence they picked.


| Variable          | Plain meaning                                                                                                                                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `weekly_deposit`  | How much cash you're putting in this round.                                                                                                                                                                              |
| `bl1_pct`         | How far below the current price the first buy order sits (e.g. 2.5% under).                                                                                                                                              |
| `bl2_pct`         | How far below the current price the second, larger buy order sits — a deeper dip than BL1.                                                                                                                               |
| `t1_pct`          | How far above the current price a normal sell order sits.                                                                                                                                                                |
| `t1_size_pct`     | How much of one week's deposit to sell, when a normal sell happens.                                                                                                                                                      |
| `ma_weeks`        | The number of past weeks averaged together to judge whether the price has "recovered."                                                                                                                                   |
| `ath_trigger_pct` | How far past the all-time high the price needs to go before the bigger profit-taking sells kick in.                                                                                                                      |
| `t2_size_pct`     | What percentage of your *total* holdings to sell when that all-time-high trigger fires.                                                                                                                                  |
| `gw_offset`       | A small buffer ($10–$100) placed just past round numbers like $100,000 or $105,000 — because lots of other traders place orders exactly *at* round numbers, so sitting just past them tends to get filled more reliably. |
| `carry_spot_pct`  | If nothing got bought last week (both limit orders missed), what percent of this week's cash to just buy at market price instead of trying again.                                                                        |


---

## Why this is useful, even if you're not technical

If you've ever dollar-cost averaged into Bitcoin, you already do the hard part — showing up consistently. This tool doesn't change that discipline. It just adds two refinements most people skip because they're tedious to do by hand every week:

- **Buying dips automatically.** Instead of buying at whatever the price is the moment you get paid, your order sits slightly below the market and waits. Some weeks it won't fill at all (price didn't dip) — that's fine, you just buy at market instead, so you're never left on the sidelines.
- **Taking small profits on the way up**, so a portion of gains gets locked in rather than just hoping a peak doesn't reverse.

You don't need to understand the mechanics to benefit from them. You need to: answer two questions each week (current price, and how much cash), copy the resulting order onto your exchange, and repeat.

---

## Giving this to someone with no technical background

### One-time setup (you do this part)

1. Run the optimizer yourself: `python main.py optimize`. This creates `params.json`.
2. Make a folder for them containing exactly these four files:
  - `weekly.py`
  - `params.json`
  - `strategy.py`
  - `config.py` (Don't include `main.py` or `backtest.py` — they don't need them and it keeps things simple.)
3. Make sure Python is installed on their computer, and that they can double-click a `.py` file to run it. On most systems, double-clicking `weekly.py` is enough. If double-clicking just opens a text editor instead of running it, set up a simple shortcut/alias that runs: `python3 weekly.py` from inside that folder — a one-time five-minute fix.

### What you tell them, verbatim

> "Whenever you want to add money to Bitcoin, open this folder and double-click `weekly.py`. It'll check the current price for you automatically. Just answer its questions — whether you're investing weekly or every two weeks, and how much you're adding — and it'll tell you exactly what to buy or sell and at what price. Copy those orders onto your exchange. At the end it'll ask whether each order actually went through — just answer honestly, since it uses that to figure out next time's orders correctly."

### What actually happens when they run it

1. It fetches the current Bitcoin price on its own (no need to look it up, unless their internet is down — then it'll ask them to type it in).
2. It asks: weekly or every two weeks?
3. It asks how much cash they're adding this time.
4. It asks how much Bitcoin they currently hold in total.
5. It prints out, in plain sentences, exactly what to buy or sell, at what price, and why — no tables, no jargon.
6. It asks two yes/no questions about what filled, then quietly saves that so next time's numbers are correct. They never have to remember or write anything down themselves.

### What they never have to do

- Install anything beyond the one-time setup
- Read or understand any code
- Track prices, dates, or past orders themselves
- Touch `main.py`, `backtest.py`, or `config.py`

### The only manual step left

They still have to copy the order details onto their exchange by hand — this tool tells you *what* to do, it doesn't place trades for you. That's intentional: it keeps a real human checking each order before money moves.