"""Per-run spend ledger: reserve before a call, record reported usage, hard stop.

Generalized from the ICSC context CostLedger. Spend counts the reported cost of
recorded calls plus the full projection of every reservation without reported
usage (a failed call may still be billable). Usage is recorded as returned,
never estimated from prompt length. JSON lines; resumable from an existing file.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import require


class SpendStop(Exception):
    """The next reservation would exceed the run's spend cap."""


class CostLedger:
    def __init__(self, path, pricing, cap_usd, *, provider, model):
        self.path = Path(path)
        self.pricing, self.cap_usd, self.provider, self.model = pricing, float(cap_usd), provider, model
        self.open = {}      # reservation id -> projected USD, no usage yet
        self.settled = 0.0  # reported (or failed-call projected) USD
        self.count = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                self._replay(json.loads(line))

    def _replay(self, event):
        kind = event["event"]
        if kind == "reserved":
            self.open[event["reservation"]] = event["projected_usd"]
            self.count = max(self.count, event["reservation"])
        elif kind == "reported":
            self.open.pop(event["reservation"])
            self.settled += event["cost_usd"]
        elif kind == "failed":
            self.settled += self.open.pop(event["reservation"])

    @property
    def spent(self):
        return self.settled + sum(self.open.values())

    def rates(self, input_tokens):
        tier = self.pricing["long_context"]
        if tier is not None and input_tokens > tier["above_input_tokens"]:
            return tier["input_per_mtok"] / 1e6, tier["output_per_mtok"] / 1e6
        return self.pricing["input_per_mtok"] / 1e6, self.pricing["output_per_mtok"] / 1e6

    def projection(self, input_cap, output_cap):
        require(type(input_cap) is int and type(output_cap) is int and input_cap > 0 and output_cap > 0, "ledger-caps")
        rate_in, rate_out = self.rates(input_cap)
        # Cache writes bill at 1.25x input; reserve as if every input token were one.
        return input_cap * rate_in * 1.25 + output_cap * rate_out

    def reserve(self, input_cap, output_cap, request_sha256):
        projected = self.projection(input_cap, output_cap)
        if self.spent + projected > self.cap_usd + 1e-12:
            self._write({"event": "stopped", "projected_usd": projected, "spent_usd": self.spent, "cap_usd": self.cap_usd})
            raise SpendStop("spend-cap-reached")
        self.count += 1
        self.open[self.count] = projected
        self._write({"event": "reserved", "reservation": self.count, "provider": self.provider, "model": self.model,
                     "input_cap": input_cap, "output_cap": output_cap, "request_sha256": request_sha256,
                     "projected_usd": projected, "spent_usd": self.spent, "cap_usd": self.cap_usd})
        return self.count

    def cost(self, usage):
        """USD for reported usage: input, cache write (1.25x) and cache read (0.1x), output (includes thinking)."""
        uncached, write, read = usage["input_tokens"], usage.get("cache_write_tokens") or 0, usage.get("cache_read_tokens") or 0
        rate_in, rate_out = self.rates(uncached + write + read)
        return uncached * rate_in + write * rate_in * 1.25 + read * rate_in * 0.1 + usage["output_tokens"] * rate_out

    def report(self, reservation, usage, request_id):
        require(reservation in self.open, "ledger-reservation-unknown")
        require(type(usage.get("input_tokens")) is int and type(usage.get("output_tokens")) is int, "provider-usage-unavailable")
        cost = self.cost(usage)
        projected = self.open.pop(reservation)
        self.settled += cost
        self._write({"event": "reported", "reservation": reservation, "request_id": request_id, "usage": usage,
                     "cost_usd": cost, "projected_usd": projected, "spent_usd": self.spent})
        return cost

    def fail(self, reservation, error, request_id=None):
        if reservation not in self.open:
            return
        self.settled += self.open.pop(reservation)
        self._write({"event": "failed", "reservation": reservation, "error": error, "request_id": request_id,
                     "spent_usd": self.spent})

    def _write(self, event):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
