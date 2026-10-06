"""Test runner for IDA's isolated Python (script dir is not on sys.path)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

loader = unittest.TestLoader()
suite = loader.loadTestsFromNames([
    "test_type6_view",
    "test_type6_world_cache",
    "test_webpage_clean_mode",
    "test_webpage_pull_rate",
    "test_radar_poll_cache",
    "test_dma_protocol",
    "test_dma_transport_state",
    "test_receiver_only",
])
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
