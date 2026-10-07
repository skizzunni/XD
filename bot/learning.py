"""A bounded paper filter, based only on completed trades before a new signal."""


class QualityLearner:
    window = 8

    def __init__(self, base_efficiency=0.40, enabled=True):
        self.base_efficiency = base_efficiency
        self.enabled = enabled
        self.samples = []
        self.seen_ids = set()

    def record(self, trade_id, closed_at, direction, net_usd):
        if trade_id in self.seen_ids:
            raise ValueError("Duplicate learning trade ID")
        self.seen_ids.add(trade_id)
        self.samples.append(
            {
                "trade_id": trade_id,
                "closed_at": closed_at,
                "direction": direction,
                "net_usd": net_usd,
            }
        )

    def state(self, direction, as_of):
        recent = sorted(
            (
                s
                for s in self.samples
                if s["direction"] == direction and s["closed_at"] < as_of
            ),
            key=lambda s: (s["closed_at"], s["trade_id"]),
        )[-self.window :]
        total = sum(s["net_usd"] for s in recent)
        tightened = self.enabled and len(recent) == self.window and total <= 0
        return {
            "direction": direction,
            "observations": len(recent),
            "recent_net_usd": total,
            "min_efficiency": min(
                0.90, self.base_efficiency + (0.15 if tightened else 0)
            ),
            "tightened": tightened,
            "rule": "eight completed trades per direction; tighten by .15 after nonpositive aggregate net; restore base after positive aggregate net",
        }
