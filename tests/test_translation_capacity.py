import os
import subprocess
import unittest
from unittest.mock import patch

from app.translation.capacity import AutomaticCapacity, MemoryMonitor, MemoryReading, memory_state, GIB
from app.translation.worker import WorkerConfig


class CapacityTest(unittest.TestCase):
    def test_formula_and_gradual_growth(self):
        for channels, expected in ((0, 0), (1, 1), (2, 1), (3, 2), (4, 2), (6, 3), (8, 4), (10, 4), (100, 4)):
            policy = AutomaticCapacity()
            for now in (0, 2, 4, 6):
                actual = policy.update(channels, 100, MemoryReading("healthy"), now=now)
            self.assertEqual(actual, expected)
        policy = AutomaticCapacity()
        self.assertEqual(policy.update(10, 10, MemoryReading("healthy"), now=0), 1)
        self.assertEqual(policy.update(10, 10, MemoryReading("healthy"), now=1), 1)
        self.assertEqual(policy.update(10, 10, MemoryReading("healthy"), now=2), 2)

    def test_empty_queue_and_reduced_channel_count_lower_limit(self):
        policy = AutomaticCapacity()
        for now in (0, 2, 4, 6):
            policy.update(10, 10, MemoryReading("healthy"), now=now)
        self.assertEqual(policy.update(2, 10, MemoryReading("healthy"), now=7), 1)
        self.assertEqual(policy.update(10, 0, MemoryReading("healthy"), now=8), 0)

    def test_memory_pressure_reduces_limit_and_requires_recovery_margin(self):
        policy = AutomaticCapacity()
        healthy = MemoryReading("healthy")
        for now in (0, 2, 4, 6):
            policy.update(10, 10, healthy, now=now)
        self.assertEqual(policy.update(10, 10, MemoryReading("low"), now=8), 2)
        self.assertEqual(policy.update(10, 10, MemoryReading("low"), now=10), 2)
        self.assertEqual(policy.update(10, 10, healthy, now=12), 2)
        self.assertEqual(policy.update(10, 10, healthy, now=20), 3)
        self.assertEqual(policy.update(10, 10, MemoryReading("critical"), now=22), 0)
        self.assertEqual(policy.update(10, 10, healthy, now=24), 0)
        self.assertEqual(policy.update(10, 10, healthy, now=32), 1)

    def test_unknown_memory_uses_one_slot_and_resource_failure_pauses(self):
        policy = AutomaticCapacity()
        self.assertEqual(policy.update(10, 10, MemoryReading("unknown"), now=0), 1)
        with patch("app.translation.capacity.time.monotonic", return_value=10):
            policy.resource_failure()
        self.assertEqual(policy.update(10, 10, MemoryReading("healthy"), now=11), 0)
        self.assertEqual(policy.update(10, 10, MemoryReading("unknown"), now=12), 0)
        self.assertEqual(policy.update(10, 10, MemoryReading("healthy"), now=40), 1)

    def test_memory_thresholds_and_invalid_measurements(self):
        self.assertEqual(memory_state(16 * GIB, 8 * GIB), "healthy")
        self.assertEqual(memory_state(16 * GIB, 1.5 * GIB), "low")
        self.assertEqual(memory_state(16 * GIB, 0.5 * GIB), "critical")
        for total, available in ((0, 0), (1, -1), (1, 2), (float("nan"), 1)):
            self.assertEqual(memory_state(total, available), "unknown")

    def test_gpu_probe_is_optional_and_handles_driver_failure(self):
        with patch("app.translation.capacity.shutil.which", return_value="nvidia-smi"):
            monitor = MemoryMonitor()
        with patch("app.translation.capacity.system_memory", return_value=(16 * GIB, 8 * GIB)):
            with patch("app.translation.capacity.subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 2)):
                result = monitor.sample()
        self.assertEqual(result.state, "healthy")
        self.assertIn("GPU memory monitoring is unavailable", result.note)

    def test_gpu_memory_pressure_and_ram_failure(self):
        with patch("app.translation.capacity.shutil.which", return_value="nvidia-smi"):
            monitor = MemoryMonitor()
        with patch("app.translation.capacity.system_memory", return_value=(16 * GIB, 8 * GIB)):
            with patch("app.translation.capacity.subprocess.run") as probe:
                probe.return_value.stdout = "8192, 256\n"
                self.assertEqual(monitor.sample().state, "low")
                monitor.sample()
                probe.assert_called_once()
        with patch("app.translation.capacity.system_memory", side_effect=OSError("Unavailable")):
            self.assertEqual(monitor.sample().state, "unknown")
        self.assertEqual(MemoryMonitor(local=False).sample().state, "unknown")

    def test_app_defaults_to_automatic_without_legacy_slot_variable(self):
        with patch.dict(os.environ, {"TRANSLATION_CONCURRENCY": "2"}):
            config = WorkerConfig.from_env()
        self.assertTrue(config.automatic)
        self.assertEqual(config.concurrency, 4)
        self.assertFalse(WorkerConfig(concurrency=2).automatic)
