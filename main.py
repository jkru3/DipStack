#!/usr/bin/env python3
"""
BTC DCA Strategy CLI
====================

Two modes:

  python main.py optimize [--objective roi|sharpe|filled]
      → fetches BTC price history, grid-searches parameter space,
        prints optimal params, saves to params.json

  python main.py orders [--params params.json] [options]
      → reads current market state (interactive prompts or --flags),
        generates this week's buy/sell orders

Run `python main.py --help` for full usage.
"""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt, Confirm, FloatPrompt

from config import DEFAULTS
from strategy import StrategyParams, WeeklyState, OrderPlan, Order, generate_orders
from backtest import fetch_weekly_prices, optimize, BacktestResult, OBJECTIVES

console = Console()
PARAMS_FILE = Path("params.json")


# ── DISPLAY HELPERS ────────────────────────────────────────────────────────────

def _fmt_usd(n: float) -> str:
    return f"${n:,.2f}"

def _fmt_btc(n: float) -> str:
    return f"{n:.6f} BTC"

def _pct(n: float) -> str:
    return f"{n:.2f}%"


def print_optimize_result(result: BacktestResult) -> None:
    p = result.params
    console.print()
    console.print(Panel.fit("[bold]Optimizer Results[/bold]", style="green"))

    # ── Three-way comparison table ──────────────────────────────────────────
    console.print("[dim]── benchmark comparison ──[/dim]")
    cmp = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    cmp.add_column("",               style="dim", min_width=20)
    cmp.add_column("Strategy",       justify="right", min_width=14)
    cmp.add_column("Pure DCA",       justify="right", min_width=14)
    cmp.add_column("Buy & Hold",     justify="right", min_width=14)

    dep = result.total_deposited_usd
    cmp.add_row("Deposited",
        _fmt_usd(dep), _fmt_usd(dep), _fmt_usd(dep))
    cmp.add_row("Final value",
        _fmt_usd(result.final_value_usd),
        _fmt_usd(result.dca_final_value_usd),
        _fmt_usd(result.bah_final_value_usd))

    def _roi_str(val, denom):
        r = (val / denom - 1) * 100 if denom else 0
        col = "green" if r >= 0 else "red"
        return f"[{col}]{_pct(r)}[/{col}]"

    cmp.add_row("Portfolio ROI",
        _roi_str(result.final_value_usd, dep),
        _roi_str(result.dca_final_value_usd, dep),
        _roi_str(result.bah_final_value_usd, dep))
    cmp.add_row("BTC accumulated",
        _fmt_btc(result.final_btc),
        _fmt_btc(result.dca_btc),
        "—")
    console.print(cmp)

    # ── Strategy-specific metrics ───────────────────────────────────────────
    console.print("[dim]── strategy metrics ──[/dim]")
    metrics = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    metrics.add_column("Metric", style="dim")
    metrics.add_column("Value")
    metrics.add_column("Notes", style="dim")

    acc_col = "green" if result.btc_accumulation_ratio >= 1 else "red"
    exc_col = "green" if result.excess_return_vs_dca >= 0 else "red"

    metrics.add_row("BTC accumulation ratio",
        f"[{acc_col}]{result.btc_accumulation_ratio:.3f}x[/{acc_col}]",
        ">1.0 means more BTC than pure DCA")
    metrics.add_row("Excess return vs DCA",
        f"[{exc_col}]{_pct(result.excess_return_vs_dca)}[/{exc_col}]",
        "strategy / DCA final value − 1")
    metrics.add_row("Max drawdown",
        f"[red]{_pct(result.max_drawdown_pct)}[/red]",
        "peak-to-trough on portfolio value")
    metrics.add_row("Sharpe proxy",
        f"{result.sharpe_proxy:.3f}",
        "ROI / (max_drawdown + 1)")
    metrics.add_row("Fill rate",
        _pct(result.fill_rate * 100),
        "limit orders filled / placed")
    metrics.add_row("Avg cash drag",
        _pct(result.avg_cash_drag),
        "mean uninvested % of portfolio")
    metrics.add_row("Weeks backtested",
        str(result.total_weeks), "")
    console.print(metrics)

    # Best params
    console.print(Panel.fit("[bold]Optimal Parameters[/bold]", style="cyan"))
    ptable = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    ptable.add_column("Parameter", style="dim")
    ptable.add_column("Value")
    ptable.add_column("Spec range", style="dim")
    rows = [
        ("BL1 discount",      f"{p.bl1_pct}%",          "1–5%"),
        ("BL2 discount",      f"{p.bl2_pct}%",          "3–15%"),
        ("Tier 1 premium",    f"{p.t1_pct}%",           "3–15%"),
        ("MA window",         f"{p.ma_weeks} weeks",    "x weeks"),
        ("T1 sell size",      f"{p.t1_size_pct}%",      "10–100% of weekly"),
        ("ATH trigger",       f"{p.ath_trigger_pct}%",  "110–130% of ATH"),
        ("T2 sell size",      f"{p.t2_size_pct}%",      "5–15% of holdings"),
        ("Gravity offset",    f"${p.gw_offset:.0f}",    "$10–$100"),
        ("Null-buy spot %",   f"{p.carry_spot_pct}%",   "0–100%"),
    ]
    for name, val, spec in rows:
        ptable.add_row(name, val, spec)
    console.print(ptable)


def print_order_plan(plan: OrderPlan, state: WeeklyState, params: StrategyParams) -> None:
    console.print()
    console.print(Panel.fit("[bold]Weekly Order Plan[/bold]", style="magenta"))

    # Context
    ctx = Table(box=box.SIMPLE_HEAD, show_header=False)
    ctx.add_column("", style="dim")
    ctx.add_column("")
    ctx.add_row("Spot",         _fmt_usd(state.spot))
    ctx.add_row("ATH",          _fmt_usd(state.ath))
    ctx.add_row(f"{params.ma_weeks}w MA", _fmt_usd(state.ma_price))
    ctx.add_row("Holdings",     _fmt_btc(state.btc_holdings))
    ctx.add_row("Weekly $",     _fmt_usd(params.weekly_deposit))
    ctx.add_row("Carry cash",   _fmt_usd(state.carry_cash))
    ctx.add_row("Sold earnings",_fmt_usd(state.sold_earnings))
    console.print(ctx)

    # Exceptions / warnings
    if plan.exceptions_triggered:
        for exc in plan.exceptions_triggered:
            console.print(f"[yellow]⚡ EXCEPTION:[/yellow] {exc}")
        console.print()

    if plan.warnings:
        for w in plan.warnings:
            console.print(f"[red]⚠  WARNING:[/red]  {w}")
        console.print()

    if not plan.orders:
        console.print("[dim]No orders generated.[/dim]")
        return

    # Orders
    tbl = Table(box=box.ROUNDED, show_header=True, header_style="bold", expand=True)
    tbl.add_column("Label",   min_width=16)
    tbl.add_column("Side",    min_width=5)
    tbl.add_column("Type",    min_width=12)
    tbl.add_column("Price",   min_width=14, justify="right")
    tbl.add_column("Qty BTC", min_width=14, justify="right")
    tbl.add_column("USD val", min_width=13, justify="right")
    tbl.add_column("Note",    min_width=20)

    for o in plan.orders:
        side_style = "green" if o.side == "BUY" else "red"
        tbl.add_row(
            o.label,
            f"[{side_style}]{o.side}[/{side_style}]",
            o.order_type,
            _fmt_usd(o.price),
            _fmt_btc(o.quantity_btc),
            _fmt_usd(o.usd_value),
            o.note,
        )

    console.print(tbl)


# ── PARAM LOADING / SAVING ─────────────────────────────────────────────────────

def load_params(path: Path) -> StrategyParams:
    if not path.exists():
        console.print(f"[yellow]No params file at {path} — using defaults[/yellow]")
        return StrategyParams(**{k: v for k, v in DEFAULTS.items()
                                 if k in StrategyParams.__dataclass_fields__})
    with open(path) as f:
        d = json.load(f)
    return StrategyParams(**d)


def save_params(params: StrategyParams, path: Path) -> None:
    import dataclasses
    with open(path, "w") as f:
        json.dump(dataclasses.asdict(params), f, indent=2)
    console.print(f"[dim]Params saved to {path}[/dim]")


# ── INTERACTIVE PROMPTS ────────────────────────────────────────────────────────

def prompt_market_state(params: StrategyParams) -> WeeklyState:
    console.print(Panel.fit("[bold]Market Inputs[/bold]", style="cyan"))
    spot     = FloatPrompt.ask("  Current BTC spot price ($)")
    ath      = FloatPrompt.ask("  All-time high ($)")
    ma_price = FloatPrompt.ask(f"  {params.ma_weeks}-week moving average ($)")
    btc_held = FloatPrompt.ask("  Total BTC holdings", default=0.0)
    console.print()
    console.print(Panel.fit("[bold]Weekly State[/bold]", style="cyan"))
    carry    = FloatPrompt.ask("  Carried-over cash from last week ($)", default=0.0)
    sold_e   = FloatPrompt.ask("  Sold earnings to redeploy ($)", default=0.0)
    b1_last  = Confirm.ask("  Buy limit 1 filled last week?", default=False)
    b2_last  = Confirm.ask("  Buy limit 2 filled last week?", default=False)

    return WeeklyState(
        spot=spot, ath=ath, ma_price=ma_price,
        btc_holdings=btc_held,
        carry_cash=carry, sold_earnings=sold_e,
        bl1_filled_last_week=b1_last,
        bl2_filled_last_week=b2_last,
    )


def state_from_args(args, params: StrategyParams) -> WeeklyState:
    """Build WeeklyState from CLI flags (non-interactive mode)."""
    missing = []
    for attr in ["spot", "ath", "ma"]:
        if getattr(args, attr, None) is None:
            missing.append(f"--{attr}")
    if missing:
        console.print(f"[red]Missing required flags: {', '.join(missing)}[/red]")
        sys.exit(1)
    return WeeklyState(
        spot=args.spot, ath=args.ath, ma_price=args.ma,
        btc_holdings=args.btc or 0.0,
        carry_cash=args.carry or 0.0,
        sold_earnings=args.sold or 0.0,
        bl1_filled_last_week=args.b1_filled,
        bl2_filled_last_week=args.b2_filled,
    )


# ── SUBCOMMANDS ────────────────────────────────────────────────────────────────

def cmd_optimize(args) -> None:
    console.print(Panel.fit(
        "[bold green]BTC DCA Optimizer[/bold green]\n"
        "[dim]Grid-searching optimal strategy parameters[/dim]"
    ))

    prices = fetch_weekly_prices(verbose=True)
    if len(prices) < 20:
        console.print("[red]Not enough price data to backtest.[/red]")
        sys.exit(1)

    result = optimize(
        prices,
        objective=args.objective,
        t2_size_pct=args.t2_size,
        verbose=True,
    )

    print_optimize_result(result)

    out_path = Path(args.output)
    save_params(result.params, out_path)
    console.print(f"\n[green]✓[/green] Run [bold]python main.py orders --params {out_path}[/bold] to use these parameters.")


def cmd_orders(args) -> None:
    console.print(Panel.fit(
        "[bold magenta]BTC DCA Order Calculator[/bold magenta]\n"
        "[dim]Generating this week's buy/sell orders[/dim]"
    ))

    params = load_params(Path(args.params))

    # Override individual params from flags if provided
    for attr in ["bl1_pct", "bl2_pct", "t1_pct", "ma_weeks",
                 "t1_size_pct", "ath_trigger_pct", "t2_size_pct",
                 "gw_offset", "carry_spot_pct", "weekly_deposit"]:
        flag_val = getattr(args, attr.replace("_pct", "").replace("_", "_"), None)
        if flag_val is not None:
            setattr(params, attr, flag_val)

    # Get market state
    if args.spot is not None:
        state = state_from_args(args, params)
    else:
        state = prompt_market_state(params)

    plan = generate_orders(state, params)
    print_order_plan(plan, state, params)


# ── ARGUMENT PARSER ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btc-dca",
        description="Bitcoin weekly DCA strategy: optimizer + order calculator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── optimize ──
    opt = sub.add_parser("optimize", help="Grid-search optimal parameters from BTC price history")
    opt.add_argument("--objective", choices=list(OBJECTIVES), default="sharpe",
                     help="Optimization objective — roi, sharpe, btc (accumulation ratio), "
                          "excess (vs DCA), filled (default: sharpe)")
    opt.add_argument("--t2-size", type=float, default=10.0,
                     help="Tier 2 ATH sell %% of holdings (default: 10, range: 5–15)")
    opt.add_argument("--output", default=str(PARAMS_FILE),
                     help=f"Where to save optimal params JSON (default: {PARAMS_FILE})")

    # ── orders ──
    ord_ = sub.add_parser("orders", help="Generate this week's orders")
    ord_.add_argument("--params", default=str(PARAMS_FILE),
                      help=f"Path to params JSON (default: {PARAMS_FILE})")

    # Market state flags (skip interactive prompts if all provided)
    ord_.add_argument("--spot",  type=float, help="Current BTC spot price ($)")
    ord_.add_argument("--ath",   type=float, help="All-time high ($)")
    ord_.add_argument("--ma",    type=float, help="Moving average price ($)")
    ord_.add_argument("--btc",   type=float, help="BTC holdings", default=0.0)
    ord_.add_argument("--carry", type=float, help="Carried-over cash ($)", default=0.0)
    ord_.add_argument("--sold",  type=float, help="Sold earnings ($)", default=0.0)
    ord_.add_argument("--b1-filled", action="store_true",
                      help="BL1 was filled last week")
    ord_.add_argument("--b2-filled", action="store_true",
                      help="BL2 was filled last week")

    # Optional param overrides for 'orders'
    ord_.add_argument("--weekly", type=float, dest="weekly_deposit",
                      help="Weekly deposit $ (overrides params file)")
    ord_.add_argument("--gw-offset", type=float, dest="gw_offset",
                      help="Gravity well offset $ (overrides params file)")

    return parser


# ── ENTRYPOINT ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    try:
        if args.command == "optimize":
            cmd_optimize(args)
        elif args.command == "orders":
            cmd_orders(args)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        if "--debug" in sys.argv:
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()