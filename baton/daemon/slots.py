"""Virtual GPU slots: a fixed number of seats on one GPU, each with a memory cap.

On the laptop: 2 slots x 2.2 GiB on a 6 GB card. On a lab server the same idea maps to whole GPUs.
The cap is enforced inside each student process with `torch.cuda.set_per_process_memory_fraction`,
so the daemon only has to hand out the fraction.
"""
from __future__ import annotations


class SlotTable:
    def __init__(self, count: int, cap_gib: float, total_bytes: int):
        self.count = count
        self.cap_gib = cap_gib
        self.cap_fraction = min(1.0, cap_gib * 2**30 / total_bytes) if total_bytes else 1.0
        self.holder: list[str | None] = [None] * count

    def free_slot(self) -> int | None:
        for i, h in enumerate(self.holder):
            if h is None:
                return i
        return None

    def assign(self, slot: int, sid: str) -> None:
        if self.holder[slot] is not None:
            raise RuntimeError(f"slot {slot} already held by {self.holder[slot]}")
        self.holder[slot] = sid

    def release(self, sid: str) -> int | None:
        for i, h in enumerate(self.holder):
            if h == sid:
                self.holder[i] = None
                return i
        return None

    def slot_of(self, sid: str) -> int | None:
        for i, h in enumerate(self.holder):
            if h == sid:
                return i
        return None

    def n_free(self) -> int:
        return sum(1 for h in self.holder if h is None)
