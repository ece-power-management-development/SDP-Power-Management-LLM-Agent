import json
import sys
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import agent_review  # noqa: E402
import local_agent  # noqa: E402


class LocalAgentTests(unittest.TestCase):
    def test_prompt_contains_context_and_recheck_rules(self):
        context = agent_review.build_demo_context()
        schema = json.loads((PROJECT / "agent_output.schema.json").read_text(encoding="utf-8"))
        prompt = local_agent.build_prompt(
            context,
            {
                "request_type": "agent_recheck",
                "operator_feedback": "Verify the missing equipment rating.",
            },
            schema,
        )
        self.assertIn("Verify the missing equipment rating", prompt)
        self.assertIn("obs:GridDyn/demo/line_N14/loading", prompt)
        self.assertIn("Never invent an equipment rating", prompt)
        self.assertIn("agent_recommendation", prompt)

    def test_local_agent_defaults_are_local(self):
        self.assertTrue(local_agent.DEFAULT_OLLAMA_BASE_URL.startswith("http://127.0.0.1"))
        self.assertTrue(local_agent.DEFAULT_CONTEXT_URL.startswith("http://127.0.0.1"))
        self.assertTrue(local_agent.DEFAULT_REVIEW_URL.startswith("http://127.0.0.1"))

    def test_local_agent_rejects_remote_urls(self):
        with self.assertRaises(ValueError):
            local_agent.require_local_url("https://example.com/v1", "Ollama URL")


if __name__ == "__main__":
    unittest.main()
