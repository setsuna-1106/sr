import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sr_agents", ROOT / "scripts" / "sr_agents.py")
sr_agents = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(sr_agents)


class SrAgentsTests(unittest.TestCase):
    def test_plan_creates_five_role_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "projects" / "demo"
            project.mkdir(parents=True)
            old_root = sr_agents.ROOT
            sr_agents.ROOT = Path(tmp)
            try:
                args = SimpleNamespace(project="demo", question="test question", run="run-1")
                sr_agents.plan(args)
                base = project / "agents" / "runs" / "run-1"
                manifest = json.loads((base / "manifest.json").read_text())
                self.assertEqual(5, len(manifest["roles"]))
                self.assertTrue((base / "tasks" / "red_team.md").is_file())
                sr_agents.validate(SimpleNamespace(project="demo", run="run-1"))
            finally:
                sr_agents.ROOT = old_root

    def test_red_team_blocks_missing_simulation_and_baseline(self):
        manifest = {"run_id": "run-1"}
        results = {
            "evidence": {"claims": [{"item_id": "i0001", "status": "supported"}], "evidence": []},
            "simulation": {"status": "blocked"},
            "baseline": {"status": "blocked"},
        }
        result = sr_agents.red_team_agent(Path("/tmp"), manifest, results)
        self.assertEqual("completed", result["status"])
        self.assertGreaterEqual(len(result["issues"]), 3)

    def test_call_role_enforces_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "projects" / "demo"
            project.mkdir(parents=True)
            old_root = sr_agents.ROOT
            sr_agents.ROOT = Path(tmp)
            try:
                sr_agents.plan(SimpleNamespace(project="demo", question="test", run="run-2"))
                sr_agents.call_role(SimpleNamespace(project="demo", run="run-2", role="baseline",
                                                     paper_ir=None, simulation_command=None, metrics=None))
                result = json.loads((project / "agents" / "runs" / "run-2" / "results" / "baseline.json").read_text())
                self.assertEqual("blocked", result["status"])
                self.assertIn("simulation", result["issues"][0]["text"])
            finally:
                sr_agents.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
