import json
import sys
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import app  # noqa: E402


class InterfaceTests(unittest.TestCase):
    def test_demo_is_explicitly_synthetic(self):
        result = app.build_demo_analysis()
        self.assertEqual(result["status"], "demo")
        self.assertEqual(result["mode"], "demo")
        self.assertTrue(any("synthetic" in item.lower() for item in result["summary"].split()))
        self.assertIn("No control command", result["report_markdown"])

    def test_analysis_record_is_json_serializable(self):
        result = app.build_demo_analysis()
        json.dumps(result)
        self.assertTrue(Path(result["report_json_path"]).is_file())
        self.assertTrue(Path(result["report_markdown_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
