"""Run-local accounting helpers for Hadron v2.3."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunCounters:
    collisions: int = 0
    saved: int = 0
    discarded: int = 0

    def reset(self) -> None:
        self.collisions = 0
        self.saved = 0
        self.discarded = 0

    def record_collision(self) -> None:
        self.collisions += 1

    def record_saved(self) -> None:
        self.saved += 1

    def record_discarded(self) -> None:
        self.discarded += 1

    def validate(self) -> "RunCounters":
        if min(self.collisions, self.saved, self.discarded) < 0:
            raise ValueError("Run counters cannot be negative.")
        if self.saved + self.discarded > self.collisions:
            raise ValueError(
                "Saved + discarded cannot exceed collision count."
            )
        return self

    def to_db_tuple(self) -> tuple[int, int, int]:
        self.validate()
        return (
            int(self.collisions),
            int(self.saved),
            int(self.discarded),
        )


__all__ = ["RunCounters"]
