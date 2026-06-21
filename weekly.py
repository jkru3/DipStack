#!/usr/bin/env python3
"""
BITCOIN BUY/SELL — THIS ROUND (double-click this file, or run: python3 weekly.py)
====================================================================================

This is the ONLY file a non-technical person needs to touch.

It fetches the current Bitcoin price automatically, then asks:
  1. Are you investing weekly or every two weeks?
  2. How much cash are you putting in this round?
  3. How much Bitcoin do you currently hold?

Then it tells you exactly what orders to place on your exchange,
in plain English, with no jargon and no tables to interpret.

It remembers what happened last time (in weekly_memory.json, created
automatically next to this file) so you don't have to track anything
yourself — just answer the questions each time you run it.

Requires: the optimizer must have been run once already by whoever set
this up, producing a "params.json" file in this same folder. If that
file is missing, this script will tell you so and stop safely.

If the automatic price check fails (no internet, price service down),
it will ask you to type the current price in instead.
"""

import json
import sys
from pathlib import Path

HERE         = Path(__file__).resolve().parent
PARAMS_FILE  = HERE / "params.json"
MEMORY_FILE  = HERE / "weekly_memory.json"

sys.path.insert(0, str(HERE))


def fail(message: str) -> None:
    print()
    print("=" * 60)
    print("  SOMETHING'S NOT SET UP RIGHT")
    print("=" * 60)
    print(f"  {message}")
    print()
    print("  Ask whoever set this up for you to fix this.")
    print("=" * 60)
    input("\nPress Enter to close...")
    sys.exit(1)


def fetch_live_btc_price() -> float | None:
    """
    Try a couple of free, no-key-required price APIs in order.
    Returns None if all of them fail (no internet, API down, etc.) —
    the caller should fall back to asking the person to type it in.
    """
    import requests

    sources = [
        ("https://api.coinbase.com/v2/prices/spot?currency=USD",
         lambda d: float(d["data"]["amount"])),
        ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
         lambda d: float(d["bitcoin"]["usd"])),
    ]
    for url, extract in sources:
        try:
            r = requests.get(url, timeout=6)
            r.raise_for_status()
            price = extract(r.json())
            if price > 0:
                return price
        except Exception:
            continue
    return None


def ask_number(prompt: str, allow_zero: bool = True) -> float:
    while True:
        raw = input(prompt).strip().replace("$", "").replace(",", "")
        try:
            val = float(raw)
            if val < 0 or (val == 0 and not allow_zero):
                print("  Please enter a positive number.")
                continue
            return val
        except ValueError:
            print("  That doesn't look like a number — try again (e.g. 105000 or 725)")


def ask_yes_no(prompt: str) -> bool:
    while True:
        raw = input(prompt + " (y/n): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please type y or n.")


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        with open(MEMORY_FILE) as f:
            return json.load(f)
    return {
        "btc_holdings": 0.0,
        "ath": 0.0,
        "price_history": [],   # last N spot prices, for the moving average
        "carry_cash": 0.0,
        "sold_earnings": 0.0,
        "bl1_filled_last_time": False,
        "bl2_filled_last_time": False,
    }


def save_memory(mem: dict) -> None:
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2)


def main() -> None:
    print()
    print("=" * 60)
    print("  BITCOIN BUY/SELL — THIS ROUND")
    print("=" * 60)
    print()

    if not PARAMS_FILE.exists():
        fail(f"Couldn't find 'params.json' in this folder ({HERE}). "
             "This file is needed to know your strategy settings.")

    try:
        from strategy import StrategyParams, WeeklyState, generate_orders
    except Exception as e:
        fail(f"Couldn't load the strategy code: {e}")

    with open(PARAMS_FILE) as f:
        params_dict = json.load(f)
    params = StrategyParams(**params_dict)

    mem = load_memory()

    # ── Get the current Bitcoin price automatically ───────────────────────────
    print("  Checking the current Bitcoin price...")
    spot = fetch_live_btc_price()
    if spot is not None:
        print(f"  Current Bitcoin price: ${spot:,.2f}")
        if not ask_yes_no("  Does that look right?"):
            spot = ask_number("  Okay — type the correct current price: $ ")
    else:
        print("  Couldn't fetch the price automatically (no internet, or the")
        print("  price service is down). You'll need to look it up yourself —")
        print("  any exchange app or a quick search for 'bitcoin price' works.")
        spot = ask_number("  Current Bitcoin price: $ ")

    # ── How often are they investing? ─────────────────────────────────────────
    print()
    print("  How often are you adding cash with this round?")
    print("    1) Weekly")
    print("    2) Every two weeks (biweekly)")
    while True:
        choice = input("  Type 1 or 2: ").strip()
        if choice in ("1", "2"):
            cadence = "weekly" if choice == "1" else "biweekly"
            break
        print("  Please type 1 or 2.")

    print()
    print(f"  How much cash are you putting in this {cadence} round?")
    deposit = ask_number("  Amount: $ ")

    print()
    btc_held = ask_number(
        f"  How much Bitcoin do you currently hold, in total? "
        f"(last known: {mem['btc_holdings']:.6f}) ",
    )

    # ── Update memory with new spot price, recompute ATH / moving average ────
    # ma_weeks in params.json was optimized assuming weekly investing rounds.
    # If this person is investing biweekly, the same real-world time span
    # covers half as many rounds — so we scale the window accordingly.
    cadence_divisor = 1 if cadence == "weekly" else 2
    ma_rounds = max(2, round(params.ma_weeks / cadence_divisor))

    mem["price_history"].append(spot)
    mem["price_history"] = mem["price_history"][-max(26, ma_rounds):]
    mem["ath"] = max(mem["ath"], spot)
    mem["cadence"] = cadence

    recent = mem["price_history"][-ma_rounds:]
    ma_price = sum(recent) / len(recent)

    state = WeeklyState(
        spot=spot,
        ath=mem["ath"],
        ma_price=ma_price,
        btc_holdings=btc_held,
        carry_cash=mem["carry_cash"],
        sold_earnings=mem["sold_earnings"],
        bl1_filled_last_week=mem["bl1_filled_last_time"],
        bl2_filled_last_week=mem["bl2_filled_last_time"],
    )

    # Use the deposit amount they typed instead of the saved default
    params.weekly_deposit = deposit

    plan = generate_orders(state, params)

    # ── Print plain-English output ───────────────────────────────────────────
    print()
    print("=" * 60)
    print("  WHAT TO DO NOW")
    print("=" * 60)

    if plan.exceptions_triggered:
        for exc in plan.exceptions_triggered:
            print(f"\n  NOTE: {exc}")

    if not plan.orders:
        print("\n  No orders to place this time.")
    else:
        for i, o in enumerate(plan.orders, start=1):
            action = "BUY" if o.side == "BUY" else "SELL"
            kind = "right now, at the current price" if o.order_type == "MARKET" \
                   else f"as a LIMIT order at ${o.price:,.0f}"
            print(f"\n  {i}. {action} {kind}")
            print(f"     Amount: {o.quantity_btc:.6f} BTC  (≈ ${o.usd_value:,.0f})")
            print(f"     Why: {o.note}")

    if plan.warnings:
        print()
        for w in plan.warnings:
            print(f"  ⚠ {w}")

    print()
    print("=" * 60)

    # ── Ask what actually got filled, to set up next time correctly ──────────
    print("\n  A FEW QUESTIONS TO SET UP NEXT TIME CORRECTLY:")
    bl1_filled = ask_yes_no("  Did 'BL1' get filled (bought) this time?")
    bl2_filled = ask_yes_no("  Did 'BL2' get filled (bought) this time?")

    # Update memory
    mem["bl1_filled_last_time"] = bl1_filled
    mem["bl2_filled_last_time"] = bl2_filled
    mem["btc_holdings"] = btc_held   # they'll re-enter the true updated total next time
    # Carry-over cash: weekly deposit not spent if neither limit filled
    if not bl1_filled and not bl2_filled:
        mem["carry_cash"] = mem.get("carry_cash", 0.0) + deposit * 0.5  # conservative default
    else:
        mem["carry_cash"] = 0.0
    mem["sold_earnings"] = 0.0  # reset; assume sells get reinvested manually for now

    save_memory(mem)

    print()
    print("  Done. Come back next time you're adding cash and run this again.")
    print("=" * 60)
    input("\nPress Enter to close...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        fail(f"Unexpected error: {e}")