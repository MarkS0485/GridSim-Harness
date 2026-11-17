import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from case_reference import calculate, load_case, write_start_case
from run import compare
from external import run_external

ROOT = Path(__file__).resolve().parent


class SmokeEvidenceTest(unittest.TestCase):
    def test_flat_start_preserves_electrical_case(self):
        source = ROOT.parent / "GridSim/data/matpower/lib/case118.m"
        original = load_case(source)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / source.name
            write_start_case(source, output)
            actual = load_case(output)
        targets = {g[0]: g[5] for g in original["gen"] if g[7] > 0}
        for bus in original["bus"]:
            bus[8] = 0.0
            if bus[1] == 1:
                bus[7] = 1.0
            elif bus[0] in targets:
                bus[7] = targets[bus[0]]
        for branch in original["branch"]:
            if branch[8] == 0 and branch[9] != 0:
                branch[8] = 1.0
        self.assertEqual(original, actual)

    def test_original_case_residual_detects_changed_transformer(self):
        source = ROOT.parent / "GridSim/data/matpower/lib/case118.m"
        case = load_case(source)
        attempt = json.loads((ROOT / "smoke/dense/attempt-01.json").read_text())
        result = attempt["result"]
        voltages = dict(zip(result["busIds"], zip(result["vm"], result["vaDeg"])))
        self.assertLess(calculate(case, voltages)[1][0], 1e-8)
        next(branch for branch in case["branch"] if branch[8] != 0)[8] *= 1.01
        self.assertGreater(calculate(case, voltages)[1][0], 1e-8)

    def test_worker_retains_failure_and_missing_attempt(self):
        source = ROOT.parent / "GridSim/data/matpower/lib/case118.m"
        case = load_case(source)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            attempt = output / "fixture/attempt-01.json"
            record = {"attempt": 1, "elapsedMs": 0.1, "converged": False}
            script = f"from pathlib import Path; Path({str(attempt)!r}).write_text({json.dumps(record)!r})"
            row, state = run_external("fixture", [sys.executable, "-c", script], output, case)
        self.assertIsNone(state)
        self.assertFalse(row["attempts"][0]["converged"])
        self.assertEqual(row["attempts"][1], {"attempt": 2, "status": "missing"})
        self.assertEqual(len(row["attempts"]), 10)

    def test_bit_comparison_detects_perturbation(self):
        attempt = json.loads((ROOT / "smoke/dense/attempt-01.json").read_text())
        modified = copy.deepcopy(attempt)
        modified["result"]["vm"][0] += 1e-12
        self.assertEqual(compare(attempt, attempt), [])
        self.assertEqual(compare(attempt, modified)[0]["location"], "vm[0]")


if __name__ == "__main__":
    unittest.main()
