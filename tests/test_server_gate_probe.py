import unittest
class ServerGateProbe(unittest.TestCase):
    def test_deliberate_failure(self):
        self.fail('EXPECTED_CURRENT_CORE_GATE_PROBE: never merge')
