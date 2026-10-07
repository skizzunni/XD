"""One-position simulated market fills. This module has no network/broker API."""

import math

from .models import TICK, TICK_VALUE, Position, Trade


class PaperBroker:
    def __init__(self, config):
        self.config = config
        self.position = None

    def quote(self, tick, side):
        if tick.bid is not None:
            reference = (tick.bid + tick.ask) / 2
            market = tick.ask if side > 0 else tick.bid
        else:
            # A discrete frozen synthetic spread; total round-trip spread is charged once.
            reference = tick.price
            market = (
                tick.price
                + (
                    math.ceil(self.config.spread_ticks / 2)
                    if side > 0
                    else -math.floor(self.config.spread_ticks / 2)
                )
                * TICK
            )
        fill = market + side * self.config.slippage_ticks_per_side * TICK
        return fill, reference

    def enter(self, tick, direction, stop, target=None):
        if self.position:
            raise RuntimeError("One open position only")
        fill, reference = self.quote(tick, direction)
        self.position = Position(
            direction,
            tick.timestamp.isoformat(),
            fill,
            reference,
            stop,
            tick.contract,
            target,
            abs(fill - stop),
            quantity=self.config.paper_contracts,
        )
        return self.position

    def exit(self, tick, session, reason):
        pos = self.position
        if pos is None:
            raise RuntimeError("No position to exit")
        fill, reference = self.quote(tick, -pos.direction)
        gross = pos.direction * (reference - pos.entry_reference) / TICK
        filled = pos.direction * (fill - pos.entry_fill) / TICK
        net = filled - self.config.round_turn_fees_usd / TICK_VALUE
        execution = gross - filled
        trade = Trade(
            str(session.day),
            self.config.strategy,
            pos.direction,
            pos.contract,
            pos.entry_time,
            tick.timestamp.isoformat(),
            pos.entry_fill,
            fill,
            gross,
            net,
            self.config.round_turn_fees_usd*pos.quantity,
            execution,
            math.ceil(execution + self.config.round_turn_fees_usd / TICK_VALUE - 1e-9),
            reason,
            session.news_flags,
            pos.quantity,
        )
        self.position = None
        return trade
