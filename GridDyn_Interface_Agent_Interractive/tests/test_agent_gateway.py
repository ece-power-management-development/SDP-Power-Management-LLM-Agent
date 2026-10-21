import json
import sys
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import agent_gateway  # noqa: E402


class AgentGatewayTests(unittest.TestCase):
    def test_demo_context_is_typed_and_read_only(self):
        state = agent_gateway.GatewayState("TestGateway", "demo")
        state.inject_demo(1.0)
        context = state.context_snapshot()
        self.assertEqual(context["packet_type"], "grid_state_context")
        self.assertEqual(context["simulation"]["time_s"], 1.0)
        self.assertTrue(context["observations"])
        self.assertEqual(context["decision_boundary"]["write_operations"], [])

    def test_claim_validation_requires_existing_evidence(self):
        state = agent_gateway.GatewayState("TestGateway", "demo")
        state.inject_demo(1.0)
        valid = state.validate_claims(
            [{"text": "The example bus voltage is 0.94 pu.", "evidence_ids": ["obs:GridDyn/demo/bus_N07/voltage"]}]
        )
        invalid = state.validate_claims(
            [{"text": "The grid will fail in five minutes.", "evidence_ids": ["invented-evidence"]}]
        )
        self.assertTrue(valid["valid"])
        self.assertFalse(invalid["valid"])

    def test_command_preview_is_always_rejected(self):
        state = agent_gateway.GatewayState("TestGateway", "helics")
        response = state.preview_command({"action": "trip_line", "asset_id": "N14"})
        self.assertFalse(response["accepted"])
        self.assertFalse(response["executed"])
        self.assertEqual(response["reason_code"], "READ_ONLY_GATEWAY")

    def test_schema_and_tool_manifest_are_valid_json(self):
        json.loads((PROJECT / "agent_context.schema.json").read_text(encoding="utf-8"))
        manifest = json.loads((PROJECT / "agent_tools.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["read_only"])
        self.assertTrue(any(item["name"] == "get_grid_context" for item in manifest["tools"]))


if __name__ == "__main__":
    unittest.main()
