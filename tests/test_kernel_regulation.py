import unittest
from unittest.mock import MagicMock, patch
from src.kernel.kernel import Kernel, SystemState
from src.core.types import SystemState as StateEnum

class TestKernelRegulation(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.mock_ce = MagicMock()
        self.mock_mem = MagicMock()
        self.mock_perceptor = MagicMock()
        self.mock_actor = MagicMock()
        
        # Mock budget status
        self.mock_llm.get_usage_status.return_value = {"remaining_requests": 1000}
        # Mock perceptor for initial cycle
        self.mock_perceptor.gather_perceptions.return_value = {"perceptions": [], "timestamp": "2026-04-02T12:00:00Z"}
        # Mock memory restore
        self.mock_mem.get_session_memory.return_value = []
        
        self.kernel = Kernel(
            llm_gateway=self.mock_llm,
            cognitive_engine=self.mock_ce,
            memory=self.mock_mem,
            perceptor=self.mock_perceptor,
            actor=self.mock_actor
        )

    def test_cognitive_load_decay(self):
        """FINDING-KERNEL-003: DMN/Reflection 完了で -0.1"""
        self.kernel._cognitive_load = 0.5
        self.kernel._urgency = 0.0  # Force IDLE
        
        # Mock DMN run
        self.mock_ce.run_dmn_cycle.return_value = {"diverged_thoughts": []}
        
        # Execute cycle (calls _phase_idle_cycle -> DMN)
        self.kernel.execute_cycle()
        self.assertAlmostEqual(self.kernel._cognitive_load, 0.4)
        
        # Mock Reflection run
        self.mock_ce.run_reflection_cycle.return_value = {"reflection_delta": {"confidence": 0.5}}
        
        # Execute next cycle (calls _phase_idle_cycle -> Reflection)
        self.kernel.execute_cycle()
        self.assertAlmostEqual(self.kernel._cognitive_load, 0.3)

    def test_metacog_threshold_sleep(self):
        """FINDING-KERNEL-004: contradiction_rate > 0.3 で強制 SleepPhase"""
        self.kernel._idle_mode = "REFLECTION"
        self.kernel._urgency = 0.0
        
        # Mock Reflection with high contradiction
        self.mock_ce.run_reflection_cycle.return_value = {
            "reflection_delta": {
                "confidence": 0.1,
                "meta_cog_eval": {"contradiction_rate": 0.4}
            }
        }
        self.mock_ce.run_sleep_phase.return_value = {}

        # Cycle 1: Reflection triggers force_sleep flag
        self.kernel.execute_cycle()
        self.assertTrue(self.kernel._force_sleep_next_cycle)
        
        # Cycle 2: Trigger forced sleep
        self.kernel.execute_cycle()
        self.assertEqual(self.kernel._system_state, StateEnum.SLEEP_PHASE)

    def test_resumption_failure_sleep(self):
        """FINDING-KERNEL-005: Resumption 回数超過で強制 SleepPhase"""
        # Mock CE failure to return final keys
        self.mock_ce.process_cognition.return_value = {"llm_requests": [{"prompt": "test"}]}
        self.mock_llm.query_openrouter.return_value = {"usage": {"total_tokens": 10}, "choices": []}
        
        # Mock urgency > 0.8
        self.mock_perceptor.gather_perceptions.return_value = {
            "perceptions": [{"source": "test", "urgency": 0.9}],
            "timestamp": "now"
        }

        # Execute cycle (will hit resume loop limit)
        self.kernel.execute_cycle()
        self.assertTrue(self.kernel._force_sleep_next_cycle)
        
        # Next cycle should trigger sleep
        self.mock_ce.run_sleep_phase.return_value = {}
        self.kernel.execute_cycle()
        self.assertEqual(self.kernel._system_state, StateEnum.SLEEP_PHASE)

    def test_variable_cognitive_load(self):
        """FINDING-KERNEL-006: System 2 実行時の負荷増加が可変であること"""
        self.kernel._cognitive_load = 0.6
        self.mock_perceptor.gather_perceptions.return_value = {
            "perceptions": [{"source": "test", "urgency": 0.5}],
            "timestamp": "now"
        }
        
        # Mock CE return load_delta = 0.15
        self.mock_ce.process_cognition.return_value = {
            "plan": {"steps": []},
            "internal_state_delta": {"cognitive_load_delta": 0.15}
        }
        
        self.kernel.execute_cycle()
        # Initial 0.6 + 0.15 = 0.75
        self.assertAlmostEqual(self.kernel._cognitive_load, 0.75)

if __name__ == '__main__':
    unittest.main()
