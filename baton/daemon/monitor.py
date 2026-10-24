"""GPU monitor: per-process compute utilization and device totals from NVML.

Per-process utilization comes from `nvmlDeviceGetProcessUtilization`, which returns the samples the
driver collected since a timestamp we pass in. A process with no samples did no compute, so it reads as
0%. Per-process *memory* is not available under Windows' WDDM driver model, so memory comes from the
students' own reports (merged in the daemon); NVML gives the device total.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GpuSample:
    util_by_pid: dict[int, int] = field(default_factory=dict)  # compute % per process (max over the interval)
    used_mib: float = 0.0
    total_mib: float = 0.0
    util_pct: int = 0
    temp_c: int = 0


class NvmlMonitor:
    def __init__(self, gpu_index: int = 0):
        import pynvml

        self.nvml = pynvml
        pynvml.nvmlInit()
        self.handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
        self.name = pynvml.nvmlDeviceGetName(self.handle)
        self._last_ts = 0

    def total_bytes(self) -> int:
        return int(self.nvml.nvmlDeviceGetMemoryInfo(self.handle).total)

    def sample(self) -> GpuSample:
        n, h = self.nvml, self.handle
        util: dict[int, int] = {}
        try:
            for s in n.nvmlDeviceGetProcessUtilization(h, self._last_ts):
                util[s.pid] = max(util.get(s.pid, 0), int(s.smUtil))
                self._last_ts = max(self._last_ts, int(s.timeStamp))
        except n.NVMLError:
            pass  # NVML_ERROR_NOT_FOUND: no samples since last time = nobody computed
        mem = n.nvmlDeviceGetMemoryInfo(h)
        try:
            temp = int(n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU))
        except n.NVMLError:
            temp = 0
        return GpuSample(util, mem.used / 2**20, mem.total / 2**20,
                         int(n.nvmlDeviceGetUtilizationRates(h).gpu), temp)

    def close(self) -> None:
        try:
            self.nvml.nvmlShutdown()
        except Exception:
            pass


class FakeMonitor:
    """Scriptable monitor for tests and for running the daemon without a GPU."""

    def __init__(self, total_mib: float = 6144.0):
        self.util_by_pid: dict[int, int] = {}
        self.total_mib = total_mib
        self.used_mib = 0.0

    def total_bytes(self) -> int:
        return int(self.total_mib * 2**20)

    def sample(self) -> GpuSample:
        return GpuSample(dict(self.util_by_pid), self.used_mib, self.total_mib,
                         max(self.util_by_pid.values(), default=0), 40)

    def close(self) -> None:
        pass
