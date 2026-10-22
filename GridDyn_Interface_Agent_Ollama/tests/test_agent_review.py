import copy
import json
import sys
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import agent_review  # noqa: E402


class AgentReviewTests(unittest.TestCase):
    def setUp(self):
        self.context = agent_review.build_demo_context()
        self.output = agent_review.build_demo_agent_output(self.context)

    def test_demo_response_is_checked_but_requires_review(self):
        result = agent_review.validate_agent_output(self.output, self.context)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["error_count"], 0)
        self.assertTrue(result["ready_for_human"])

    def test_missing_evidence_blocks_response(self):
        output = copy.deepcopy(self.output)
        output["claims"][0]["evidence_ids"] = ["obs:not-present"]
        result = agent_review.validate_agent_output(output, self.context)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["ready_for_human"])
        self.assertTrue(any(item["code"] == "evidence_missing" for item in result["checks"]))

    def test_command_fields_and_missing_approval_block_response(self):
        output = copy.deepcopy(self.output)
        output["possible_solutions"][0]["approval_required"] = False
        output["possible_solutions"][0]["command"] = {"action": "trip_line"}
        result = agent_review.validate_agent_output(output, self.context)
        self.assertEqual(result["status"], "blocked")
        codes = {item["code"] for item in result["checks"] if item["status"] == "fail"}
        self.assertIn("approval_required", codes)
        self.assertIn("direct_actuation", codes)

    def test_blocked_response_cannot_be_accepted(self):
        output = copy.deepcopy(self.output)
        output["summary_evidence_ids"] = ["invented"]
        store = agent_review.ReviewStore()
        store.submit({"context": self.context, "output": output})
        with self.assertRaises(ValueError):
            store.record_review({"decision": "accept"})

    def test_operator_feedback_creates_recheck_request(self):
        store = agent_review.ReviewStore()
        state = store.submit({"context": self.context, "output": self.output})
        self.assertEqual(state["validation"]["status"], "needs_review")
        state = store.record_review(
            {
                "decision": "request_recheck",
                "issue_types": ["missing_context", "unsupported_claim"],
                "feedback": "Verify the equipment rating before describing a limit violation.",
            }
        )
        request = state["recheck_request"]
        self.assertEqual(request["status"], "pending_agent_recheck")
        self.assertEqual(request["previous_response_id"], self.output["response_id"])
        self.assertTrue(request["required_actions"])

    def test_review_contract_assets_are_valid_json(self):
        output_schema = json.loads((PROJECT / "agent_output.schema.json").read_text(encoding="utf-8"))
        tools = json.loads((PROJECT / "agent_review_tools.json").read_text(encoding="utf-8"))
        self.assertEqual(output_schema["properties"]["output_type"]["const"], "agent_recommendation")
        self.assertTrue(tools["read_only_to_grid"])
        self.assertTrue(any(tool["name"] == "get_recheck_request" for tool in tools["tools"]))


if __name__ == "__main__":
    unittest.main()
