"""Bounded paper sizing from completed, risk-normalized position outcomes."""

from collections import deque
import math


def adaptive_state(outcomes, start=2, maximum=10):
    """Replay chronological outcomes; quantities never enter the evidence score.

    Net R includes fees divided by the original total stop risk. Faulted outcomes
    reduce size but cannot help earn a promotion. Callers must filter by profile
    and strictly-before decision time; partial exits are never separate samples.
    """
    quantity, count, streak, since_reduction = start, 0, 0, 20
    recent = deque(maxlen=20)
    weak_latched = False
    reason = "BUILDING_RECORD"
    net = drawdown = positive = negative = 0.0
    qualifies = False
    for sample in outcomes:
        value = sample["net_r"]
        if not sample["eligible"]:
            quantity = max(1, quantity // 2)
            since_reduction = 0
            streak = 0
            reason = "FAULT_REDUCTION"
            continue
        count += 1
        since_reduction += 1
        recent.append(value)
        streak = streak + 1 if value < 0 else 0
        reason = "BUILDING_RECORD" if count < 20 else "HOLD_SIZE"
        if streak >= 2:
            quantity = max(1, quantity // 2 if streak == 2 else quantity - 1)
            since_reduction = 0
            reason = "LOSS_STREAK_REDUCTION"
        net = sum(recent)
        positive = sum(v for v in recent if v > 0)
        negative = -sum(v for v in recent if v < 0)
        equity = peak = drawdown = 0.0
        for v in recent:
            equity += v
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        weak = len(recent) == 20 and (net <= 0 or drawdown >= 3)
        if weak and not weak_latched:
            quantity = max(1, quantity // 2)
            since_reduction = 0
            reason = "WEAK_WINDOW_REDUCTION"
        weak_latched = weak
        qualifies = (len(recent) == 20 and net >= 4
                     and (negative == 0 or positive >= 1.5 * negative)
                     and sum(v > 0 for v in recent) >= 8
                     and net - max(recent) > 0 and drawdown < 3)
        if count % 20 == 0 and qualifies and value > 0 and since_reduction >= 5:
            reason = "PROFITABLE_BLOCK_INCREASE" if quantity < maximum else "AT_MAXIMUM"
            quantity = min(maximum, quantity + 1)
    return {"suggested_contracts": quantity, "observations": count,
            "recent_net_r": net, "recent_drawdown_r": drawdown,
            "positive_r": positive, "negative_r": negative,
            "growth_eligible": qualifies and streak == 0 and since_reduction >= 5,
            "next_review_in": 20 - count % 20, "reason": reason}


def budget_quantity(suggested, per_contract_reservation, remaining):
    if not math.isfinite(per_contract_reservation) or per_contract_reservation <= 0:
        raise ValueError("A finite positive per-contract risk reservation is required")
    if not math.isfinite(remaining):
        raise ValueError("Remaining risk budget must be finite")
    quantity = min(suggested, max(0, math.floor(remaining / per_contract_reservation)))
    # Keep the actual cash inequality strict even at floating-point boundaries.
    while quantity > 0 and quantity * per_contract_reservation > remaining:
        quantity -= 1
    return quantity


class AdaptiveSizer:
    def __init__(self, start=2, maximum=10):
        self.start, self.maximum = start, maximum
        self.samples, self.ids = [], set()

    def record(self, trade_id, closed_at, profile, net_usd, initial_risk_usd, eligible=True):
        if trade_id in self.ids:
            raise ValueError("Duplicate sizing position ID")
        if (not math.isfinite(net_usd) or not math.isfinite(initial_risk_usd)
                or initial_risk_usd <= 0):
            raise ValueError("Sizing requires finite net P&L and positive original stop risk")
        self.ids.add(trade_id)
        self.samples.append({"trade_id": trade_id, "closed_at": closed_at,
                             "profile": profile, "net_r": net_usd / initial_risk_usd,
                             "eligible": eligible})

    def state(self, profile, as_of):
        outcomes = sorted((s for s in self.samples
                           if s["profile"] == profile and s["closed_at"] < as_of),
                          key=lambda s: (s["closed_at"], s["trade_id"]))
        state = adaptive_state(outcomes, self.start, self.maximum)
        state["sizing_reason"] = state.pop("reason")
        return {"profile": profile, "starting_contracts": self.start,
                "maximum_contracts": self.maximum,
                **state}
