"""Unit tests for the multi-case suite runner. Run: python -m unittest test_suite"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import importlib.util

from run import CASES, executable

_spec = importlib.util.spec_from_file_location("run_suite", Path(__file__).with_name("run-suite.py"))
run_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_suite)


def matpower_tree(root, names, altered=()):
    data = root / "data"
    data.mkdir(parents=True)
    for name in names:
        # Bytes with the pinned digest are not available offline, so tests patch CASES.
        (data / name).write_bytes(b"altered" if name in altered else name.encode())
    return root


class CatalogueTest(unittest.TestCase):
    def test_catalogue_pins_every_case(self):
        self.assertEqual(len(CASES), 78)
        self.assertEqual(sum(entry["buses"] <= 5000 for entry in CASES.values()), 67)
        for name, entry in CASES.items():
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$", name)
        self.assertEqual(CASES["case118.m"]["buses"], 118)
        self.assertEqual(CASES["case1354pegase.m"]["sha256"],
                         "1b08b25a2f6c1d540d090009dfaff41ff2b05784a2d8d302a7ad695821557b89")


class SelectCasesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.saved = dict(CASES)
        self.addCleanup(lambda: (CASES.clear(), CASES.update(self.saved)))
        CASES.clear()
        for name, buses in (("caseA.m", 30), ("caseB.m", 5), ("caseC.m", 9000)):
            CASES[name] = dict(buses=buses, sha256=hashlib.sha256(name.encode()).hexdigest())

    def test_orders_by_buses_and_applies_limit(self):
        tree = matpower_tree(self.root / "m", ["caseA.m", "caseB.m", "caseC.m"])
        chosen, unlisted = run_suite.select_cases(tree, 5000)
        self.assertEqual(chosen, [(5, "caseB.m"), (30, "caseA.m")])
        self.assertEqual(unlisted, [])

    def test_unlisted_data_cases_are_reported_not_run(self):
        tree = matpower_tree(self.root / "m", ["caseA.m", "caseB.m", "caseNew.m", "contab_x.m"])
        chosen, unlisted = run_suite.select_cases(tree, 5000)
        self.assertEqual([name for _, name in chosen], ["caseB.m", "caseA.m"])
        self.assertEqual(unlisted, ["caseNew.m"])

    def test_altered_case_is_refused(self):
        tree = matpower_tree(self.root / "m", ["caseA.m", "caseB.m"], altered={"caseB.m"})
        with self.assertRaisesRegex(ValueError, "do not match"):
            run_suite.select_cases(tree, 5000)

    def test_missing_case_is_refused(self):
        tree = matpower_tree(self.root / "m", ["caseA.m"])
        with self.assertRaisesRegex(ValueError, "Missing"):
            run_suite.select_cases(tree, 5000)

    def test_unpinned_name_is_refused(self):
        tree = matpower_tree(self.root / "m", ["caseA.m"])
        with self.assertRaisesRegex(ValueError, "not pinned"):
            run_suite.select_cases(tree, 5000, names=["caseZ.m"])

    def test_explicit_names_ignore_bus_limit(self):
        tree = matpower_tree(self.root / "m", ["caseA.m", "caseB.m", "caseC.m"])
        chosen, _ = run_suite.select_cases(tree, 10, names=["caseC.m"])
        self.assertEqual(chosen, [(9000, "caseC.m")])


class ResultsTest(unittest.TestCase):
    def test_unsupported_reasons(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "materialise.log"
            log.write_text("case_RTS_GMLC.m: Case has unsupported electrical field dcline\nnoise\n",
                           encoding="utf-8")
            self.assertEqual(run_suite.unsupported_reasons(log),
                             {"case_RTS_GMLC.m": "Case has unsupported electrical field dcline"})
            self.assertEqual(run_suite.unsupported_reasons(Path(temp) / "none.log"), {})

    def test_case_rows_status_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / "materialised").mkdir()
            (temp / "materialised" / "caseA.m").write_text("x")
            folder = temp / "caseA"
            folder.mkdir()
            good = [dict(converged=True, elapsedMs=2.0, referenceMismatchPu=[1e-12, 1, "p"])
                    for _ in range(10)]
            manifest = dict(paths=[dict(mode="dense", attempts=good),
                                   dict(mode="sparse", attempts=good[:5])])
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            rows = {row[2]: row for row in
                    run_suite.case_rows("caseA.m", 30, folder, temp / "materialised", None, 0)}
            self.assertEqual(rows["dense"][3:7], ("complete", 10, 2.0, 1e-12))
            self.assertEqual(rows["sparse"][3], "incomplete")
            self.assertEqual(rows["pypsa"][3], "no evidence")
            self.assertEqual(len(rows), 9)
            skipped = run_suite.case_rows("caseR.m", 73, temp / "caseR", temp / "materialised",
                                          "Case has unsupported electrical field dcline", "")
            self.assertEqual({row[3] for row in skipped}, {"case not materialised"})
            self.assertEqual(skipped[0][8], "Case has unsupported electrical field dcline")


class ExecutableTest(unittest.TestCase):
    def test_symlink_is_not_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "real-python"
            target.write_text("#!/bin/sh\n")
            link = Path(temp) / "venv-python"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            self.assertEqual(executable(link), link.absolute())
            self.assertNotEqual(executable(link), target.resolve())


if __name__ == "__main__":
    unittest.main()
