"""Conservative automatic scheduling; this does not configure Ollama's server."""

import ctypes
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


MAX_AUTOMATIC_SLOTS = 4
GIB = 1024 ** 3


@dataclass(frozen=True)
class MemoryReading:
    state: str  # healthy, low, critical or unknown
    note: str = ""

    def __post_init__(self):
        if self.state not in ("healthy", "low", "critical", "unknown"):
            raise ValueError("Invalid memory state.")


def memory_state(total, available):
    if not math.isfinite(total) or not math.isfinite(available) or total <= 0 or available < 0 or available > total:
        return "unknown"
    if available < max(512 * 1024 ** 2, total * 0.05):
        return "critical"
    if available < max(GIB, total * 0.10):
        return "low"
    # A recovery margin prevents repeatedly raising/lowering the limit.
    if available < max(2 * GIB, total * 0.15):
        return "low"
    return "healthy"


def system_memory():
    """Return total/available bytes without adding a platform-specific dependency."""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total", "available", "page_total", "page_available",
                    "virtual_total", "virtual_available", "extended_available",
                )
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Memory information is unavailable.")
        return status.total, status.available
    # MemAvailable includes reclaimable memory rather than counting only free pages.
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0]) * 1024
    return values["MemTotal"], values["MemAvailable"]


class MemoryMonitor:
    def __init__(self, *, local=True):
        self.local = local
        self.gpu_command = shutil.which("nvidia-smi") if local else None
        self.gpu_states = []
        self.next_gpu_sample = 0.0

    def sample(self):
        if not self.local:
            return MemoryReading("unknown", "Ollama's computer memory cannot be monitored here; using one translation at a time.")
        try:
            ram = memory_state(*system_memory())
        except (OSError, ValueError, KeyError, AttributeError):
            ram = "unknown"
        if self.gpu_command and time.monotonic() >= self.next_gpu_sample:
            self.next_gpu_sample = time.monotonic() + 10
            self.gpu_states = []
            try:
                output = subprocess.run(
                    [self.gpu_command, "--query-gpu=memory.total,memory.free", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, check=True, timeout=2,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                ).stdout
                for line in output.splitlines():
                    total, available = (float(value.strip()) * 1024 ** 2 for value in line.split(","))
                    self.gpu_states.append(memory_state(total, available))
            except (OSError, ValueError, subprocess.SubprocessError):
                self.gpu_states = []
        gpu_states = self.gpu_states
        if ram == "unknown":
            return MemoryReading("unknown", "Memory monitoring is unavailable; using one translation at a time.")
        if ram == "critical":
            return MemoryReading("critical", "Computer memory is nearly full. New translations are paused while current jobs finish.")
        if ram == "low":
            return MemoryReading("low", "Computer memory is low. Fewer new translations will start until it recovers.")
        # GPU memory can be preallocated by Ollama. Keep one job moving when only
        # GPU free memory is low, rather than indefinitely stopping a loaded model.
        if any(state in ("critical", "low") for state in gpu_states):
            return MemoryReading("low", "GPU memory has limited headroom. Translation concurrency is reduced.")
        if not gpu_states or "unknown" in gpu_states:
            return MemoryReading("healthy", "RAM monitoring is active. GPU memory monitoring is unavailable.")
        return MemoryReading("healthy")


class AutomaticCapacity:
    """Ramp up at most once every two seconds; back off and recover gradually."""

    def __init__(self):
        self.limit = 0
        self.next_increase = 0.0
        self.pressure = None

    def resource_failure(self):
        self.limit = 0
        self.next_increase = time.monotonic() + 30
        self.pressure = "resource_failure"

    def update(self, channels, outstanding, memory, *, now=None):
        now = time.monotonic() if now is None else now
        desired = min((channels + 1) // 2, outstanding, MAX_AUTOMATIC_SLOTS)
        if desired == 0:
            self.limit = 0
            # Keep an existing pressure cooldown even after the queue empties.
            return 0
        if self.pressure == "resource_failure" and now < self.next_increase:
            self.limit = 0
            return 0
        if memory.state == "critical":
            self.limit = 0
            self.next_increase = now + 10
            self.pressure = "critical"
        elif memory.state == "unknown":
            self.limit = min(desired, 1)
            self.next_increase = now + 10
            self.pressure = "unknown"
        elif memory.state == "low":
            if self.pressure != "low":
                self.limit = max(1, self.limit // 2)
            self.limit = min(desired, max(1, self.limit))
            self.next_increase = now + 10
            self.pressure = "low"
        else:
            self.pressure = None
            self.limit = min(self.limit, desired)
            if self.limit == 0 and now >= self.next_increase:
                self.limit = 1
                self.next_increase = now + 2
            elif self.limit < desired and now >= self.next_increase:
                self.limit += 1
                self.next_increase = now + 2
        return self.limit
