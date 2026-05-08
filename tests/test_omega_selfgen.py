"""Ω_selfgen 経路のテスト: omega_meta 接続、φ_corr 限定実行、内発的 urgency 注入"""
import unittest
from unittest.mock import MagicMock, patch, call
from src.kernel.orchestrator import KernelOrchestrator
from src.kernel.state_manager import KernelStateManager


def _make_orchestrator():
    """テスト用の KernelOrchestrator を生成する。"""
    state = KernelStateManager()
    mediator = MagicMock()
    # デフォルト: perceptor → urgency=0 (IDLE)
    mediator.route_action.return_value = {
        "success": True,
        "result": {"perceptions": [], "timestamp": "t0"},
    }
    orch = KernelOrchestrator(state, mediator)
    return orch, state, mediator


class TestOmegaMetaIntegration(unittest.TestCase):
    """(A) Reflection の omega_meta が goal_omega サブステップに統合されることを検証。"""

    def test_omega_meta_integrates_prioritized_goals(self):
        orch, state, mediator = _make_orchestrator()
        state.goal_omega = {
            "description": "既存目標",
            "achievement_condition": "",
            "progress": 0.0,
            "sub_steps": [{"description": "既存ステップ", "achievement_condition": "", "status": "PENDING"}],
        }
        orch._idle_mode = "REFLECTION"

        # memory.get_session_memory
        def side_route(module, action, params):
            if action == "get_session_memory":
                return {"success": True, "result": []}
            if action == "get_capabilities":
                return {"success": True, "result": {}}
            if action == "execute_actions":
                return {"success": True, "execution_result": "OK"}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "reflection_delta": {"confidence": 0.5},
            "omega_meta": {
                "prioritized_goals": ["新目標A", "新目標B"],
                "consistency_check_result": {
                    "af_v_alignment": 0.8,
                    "session_coherence": 0.7,
                },
                "emergent_directions": ["探索方向X"],
            },
            "correction_steps": [],
        }

        orch._phase_idle_cycle(1.0)

        # サブステップが統合されたことを確認
        subs = state.goal_omega["sub_steps"]
        descs = [s["description"] for s in subs]
        self.assertIn("既存ステップ", descs)  # 既存は保持
        self.assertIn("新目標A", descs)
        self.assertIn("新目標B", descs)
        self.assertIn("探索方向X", descs)
        # 内発的urgencyが注入されたことを確認
        self.assertGreater(orch._intrinsic_urgency, 0.0)

    def test_omega_meta_skipped_when_low_consistency(self):
        orch, state, mediator = _make_orchestrator()
        state.goal_omega = {
            "description": "目標",
            "achievement_condition": "",
            "progress": 0.0,
            "sub_steps": [],
        }
        orch._idle_mode = "REFLECTION"

        def side_route(module, action, params):
            if action == "get_session_memory":
                return {"success": True, "result": []}
            if action == "get_capabilities":
                return {"success": True, "result": {}}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "reflection_delta": {"confidence": 0.5},
            "omega_meta": {
                "prioritized_goals": ["却下される目標"],
                "consistency_check_result": {
                    "af_v_alignment": 0.3,  # 閾値以下
                    "session_coherence": 0.2,  # 閾値以下
                },
            },
            "correction_steps": [],
        }

        orch._phase_idle_cycle(1.0)

        # サブステップが変更されていないことを確認
        self.assertEqual(len(state.goal_omega["sub_steps"]), 0)
        self.assertEqual(orch._intrinsic_urgency, 0.0)

    def test_omega_meta_dedup_existing_substeps(self):
        orch, state, mediator = _make_orchestrator()
        state.goal_omega = {
            "description": "目標",
            "achievement_condition": "",
            "progress": 0.0,
            "sub_steps": [{"description": "重複目標", "achievement_condition": "", "status": "IN_PROGRESS"}],
        }
        orch._idle_mode = "REFLECTION"

        def side_route(module, action, params):
            if action == "get_session_memory":
                return {"success": True, "result": []}
            if action == "get_capabilities":
                return {"success": True, "result": {}}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "reflection_delta": {"confidence": 0.5},
            "omega_meta": {
                "prioritized_goals": ["重複目標"],  # 既に存在する
                "consistency_check_result": {"af_v_alignment": 0.9, "session_coherence": 0.8},
            },
            "correction_steps": [],
        }

        orch._phase_idle_cycle(1.0)
        # 重複が追加されていないことを確認
        self.assertEqual(len(state.goal_omega["sub_steps"]), 1)


class TestPhiCorrExecution(unittest.TestCase):
    """(B) φ_corr: correction_steps の限定的実行を検証。"""

    def test_phi_corr_executes_notify_only(self):
        orch, state, mediator = _make_orchestrator()
        orch._idle_mode = "REFLECTION"

        def side_route(module, action, params):
            if action == "get_session_memory":
                return {"success": True, "result": []}
            if action == "get_capabilities":
                return {"success": True, "result": {}}
            if action == "execute_actions":
                return {"success": True, "execution_result": "OK"}
            if action == "store_event":
                return {"success": True, "result": {}}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "reflection_delta": {"confidence": 0.8},  # > 0.7 threshold
            "correction_steps": [
                {"action_type": "NOTIFY", "params": {"NOTIFY_PARAMS": {"recipient": "USER", "message": "補正通知"}}},
                {"action_type": "FILE_WRITE", "params": {"FILE_WRITE_PARAMS": {"target_path": "/tmp/danger.txt", "content": "x"}}},
            ],
        }

        orch._phase_idle_cycle(1.0)

        # execute_actions が呼ばれた場合、NOTIFYのみが渡されていることを確認
        execute_calls = [
            c for c in mediator.route_action.call_args_list
            if c[0][1] == "execute_actions"
        ]
        if execute_calls:
            actions = execute_calls[0][0][2]["actions"]
            for a in actions:
                self.assertEqual(a["action_type"], "NOTIFY")

    def test_phi_corr_skips_when_low_confidence(self):
        orch, state, mediator = _make_orchestrator()
        orch._idle_mode = "REFLECTION"

        def side_route(module, action, params):
            if action == "get_session_memory":
                return {"success": True, "result": []}
            if action == "get_capabilities":
                return {"success": True, "result": {}}
            if action == "execute_actions":
                return {"success": True, "execution_result": "OK"}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "reflection_delta": {"confidence": 0.3},  # < 0.7 threshold
            "correction_steps": [
                {"action_type": "NOTIFY", "params": {"NOTIFY_PARAMS": {"recipient": "USER", "message": "低信頼"}}},
            ],
        }

        orch._phase_idle_cycle(1.0)

        # execute_actions が呼ばれていないことを確認
        execute_calls = [
            c for c in mediator.route_action.call_args_list
            if c[0][1] == "execute_actions"
        ]
        self.assertEqual(len(execute_calls), 0)


class TestIntrinsicUrgency(unittest.TestCase):
    """(C) 内発的 urgency 注入: IDLE での目標更新が次サイクルの Gate を ACTIVE にすることを検証。"""

    def test_dmn_goal_update_injects_urgency(self):
        orch, state, mediator = _make_orchestrator()
        orch._idle_mode = "DMN"

        def side_route(module, action, params):
            if action == "query_memory":
                return {"success": True, "result": {"results": []}}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "diverged_thoughts": [{
                "thought_id": "t1",
                "content": "新しいアイデア",
                "potential_goal_delta": {
                    "description": "DMNで発見した目標",
                    "priority": 0.6,  # >= 0.4 threshold
                    "category": "Ω潜性",
                },
            }],
            "integration_delta": {"new_perspectives": [], "confidence": 0.5},
        }

        orch._phase_idle_cycle(1.0)

        # intrinsic_urgency が注入されたことを確認
        self.assertAlmostEqual(orch._intrinsic_urgency, 0.6)
        # goal_omega が更新されたことを確認
        self.assertEqual(state.goal_omega["description"], "DMNで発見した目標")

    def test_intrinsic_urgency_merged_into_gate(self):
        orch, state, mediator = _make_orchestrator()
        orch._intrinsic_urgency = 0.5  # 前サイクルのIDLEで蓄積

        # perceptor → urgency=0 (外部刺激なし)
        def side_route(module, action, params):
            if action == "gather_perceptions":
                return {"success": True, "result": {"perceptions": [], "timestamp": "t1"}}
            if action == "get_session_memory":
                return {"success": True, "result": []}
            if action == "get_capabilities":
                return {"success": True, "result": {}}
            return {"success": True, "result": {}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "plan": {"steps": []},
            "internal_state_delta": {"urgency": 0.5},
            "goal_omega": state.goal_omega,
        }

        result = orch.execute_cycle()

        # urgency が intrinsic_urgency から注入されたことを確認
        self.assertGreater(state.urgency, 0.0)
        # intrinsic_urgency は消費されたことを確認
        self.assertEqual(orch._intrinsic_urgency, 0.0)
        # IDLE ではなく ACTIVE パスに入ったことを確認
        self.assertEqual(result["outcome"], "SUCCESS")

    def test_no_intrinsic_urgency_stays_idle(self):
        orch, state, mediator = _make_orchestrator()
        orch._intrinsic_urgency = 0.0  # 蓄積なし

        def side_route(module, action, params):
            if action == "query_memory":
                return {"success": True, "result": {"results": []}}
            return {"success": True, "result": {"perceptions": [], "timestamp": "t0"}}

        mediator.route_action.side_effect = side_route
        mediator.run_cognition_with_resumption.return_value = {
            "diverged_thoughts": [],
            "integration_delta": {"new_perspectives": [], "confidence": 0.0},
        }

        result = orch.execute_cycle()

        # urgency = 0 なので IDLE のまま
        self.assertEqual(state.urgency, 0.0)


if __name__ == "__main__":
    unittest.main()
