"""Test runner for IDA's isolated Python (script dir is not on sys.path)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

loader = unittest.TestLoader()
suite = loader.loadTestsFromName("test_type6_view")
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
