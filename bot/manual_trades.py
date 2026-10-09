"""Cost-aware analysis for exported completed MNQ positions."""

from dataclasses import dataclass
from datetime import datetime
import csv
import math
from pathlib import Path


@dataclass(frozen=True)
class ManualTrade:
    trade_id: str
    entry_time: datetime
    exit_time: datetime
    direction: str
    quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    commissions: float
    fees: float

    @property
    def net_pnl(self):
        return self.gross_pnl - self.commissions - self.fees


def _number(value):
    cleaned = str(value).strip().replace("$", "").replace(",", "").replace("USD", "")
    cleaned = cleaned.replace("(", "-").replace(")", "")
    return float(cleaned)


def read_topstep_positions(path):
    trades, ids = [], set()
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            lowered = {key.strip().lower(): value for key, value in row.items() if key}
            def get(*names):
                return next((lowered[name] for name in names if name in lowered), None)
            trade_id = str(get("id", "trade id", "trade_id") or "").strip()
            if not trade_id or trade_id in ids:
                raise ValueError("Every exported position needs a unique ID")
            ids.add(trade_id)
            direction = str(get("direction") or "").strip().lower()
            if direction not in {"long", "short"}:
                raise ValueError(f"Invalid direction for {trade_id}")
            def parse_time(value):
                return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            trade = ManualTrade(
                trade_id, parse_time(get("entry time", "entry_time")),
                parse_time(get("exit time", "exit_time")), direction,
                int(_number(get("size", "quantity"))),
                _number(get("entry price", "entry_price")),
                _number(get("exit price", "exit_price")),
                _number(get("p&l", "pnl", "gross pnl", "gross_pnl")),
                _number(get("commissions") or 0), _number(get("fees") or 0),
            )
            expected = (1 if direction == "long" else -1) * (trade.exit_price-trade.entry_price) * 2 * trade.quantity
            if not math.isclose(expected, trade.gross_pnl, abs_tol=.011):
                raise ValueError(f"MNQ price/P&L mismatch for {trade_id}")
            if trade.exit_time < trade.entry_time or trade.quantity < 1:
                raise ValueError(f"Invalid timing/quantity for {trade_id}")
            trades.append(trade)
    if not trades:
        raise ValueError("No completed positions found")
    return sorted(trades, key=lambda trade: (trade.exit_time, trade.trade_id))


def summarize_manual_trades(trades):
    if not trades:
        raise ValueError("At least one trade is required")
    nets = [trade.net_pnl for trade in trades]
    gross = [trade.gross_pnl for trade in trades]
    winners = [value for value in nets if value > 0]
    losers = [value for value in nets if value < 0]
    equity = peak = drawdown = 0.0
    for value in nets:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    costs = sum(trade.commissions+trade.fees for trade in trades)
    positive, negative = sum(winners), -sum(losers)
    return {
        "completed_positions": len(trades),
        "gross_pnl_usd": round(sum(gross), 2),
        "costs_usd": round(costs, 2),
        "net_pnl_usd": round(sum(nets), 2),
        "win_rate": len(winners)/len(trades),
        "net_profit_factor": positive/negative if negative else None,
        "mean_win_usd": positive/len(winners) if winners else None,
        "mean_loss_usd": negative/len(losers) if losers else None,
        "largest_win_usd": max(nets),
        "largest_loss_usd": min(nets),
        "closed_trade_drawdown_usd": round(drawdown, 2),
        "mean_holding_seconds": sum((t.exit_time-t.entry_time).total_seconds() for t in trades)/len(trades),
        "cost_share_of_gross_gains": costs/sum(v for v in gross if v > 0) if any(v > 0 for v in gross) else None,
    }
