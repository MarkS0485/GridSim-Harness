import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from run import scheduling
from scheduler import Job, PINNED_ENVIRONMENT, is_linux, parse_cores, physical_order, run_jobs

PROBE = ("import json, os, sys; from pathlib import Path; "
         "Path(sys.argv[1]).write_text(json.dumps({'cpus': sorted(os.sched_getaffinity(0)) "
         "if hasattr(os, 'sched_getaffinity') else None, 'omp': os.environ.get('OMP_NUM_THREADS'), "
         "'dotnet': os.environ.get('DOTNET_PROCESSOR_COUNT')}))")


def quiet(*args, **kwargs):
    pass


class SchedulerTest(unittest.TestCase):
    def test_parse_cores(self):
        self.assertEqual(parse_cores("3,1-2, 5-6,2"), [1, 2, 3, 5, 6])
        for bad in ("", "4-2", "-1", "a"):
            with self.assertRaises(ValueError):
                parse_cores(bad)

    def test_physical_order_puts_smt_siblings_last(self):
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            for cpu, core in ((0, 0), (1, 1), (2, 0), (3, 1), (4, 2)):
                topology = sysfs / f"cpu{cpu}/topology"
                topology.mkdir(parents=True)
                (topology / "physical_package_id").write_text("0\n")
                (topology / "core_id").write_text(f"{core}\n")
            self.assertEqual(physical_order([0, 1, 2, 3, 4], sysfs), [0, 1, 4, 2, 3])
            self.assertEqual(physical_order([7, 8], sysfs), [7, 8])

    def test_scheduling_description(self):
        self.assertIn("one after another, unpinned", scheduling({}))
        self.assertIn("pinned to CPU 4", scheduling({"scheduler": {"cores": [4]}}))
        self.assertIn("up to 2 paths", scheduling({"scheduler": {"cores": [1, 2]}}))

    def test_sequential_run_inherits_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            probe = folder / "probe.json"
            outcome = run_jobs([Job("probe", [sys.executable, "-c", PROBE, str(probe)],
                                    folder, "worker.log")], log=quiet)["probe"]
            self.assertEqual(outcome["exitCode"], 0)
            self.assertNotIn("cpuCore", outcome)
            seen = json.loads(probe.read_text())
            self.assertEqual(seen["omp"], os.environ.get("OMP_NUM_THREADS"))

    @unittest.skipUnless(is_linux(), "pinning needs Linux")
    def test_parallel_jobs_are_pinned_to_distinct_cpus(self):
        allowed = sorted(os.sched_getaffinity(0))
        cores = allowed[-2:]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            jobs = [Job(f"probe{i}", [sys.executable, "-c", PROBE, str(folder / f"probe{i}.json")],
                        folder, f"probe{i}.log") for i in range(4)]
            outcomes = run_jobs(jobs, cores, log=quiet)
            self.assertEqual(list(outcomes), [job.name for job in jobs])
            for i, outcome in outcomes.items():
                seen = json.loads((folder / f"{i}.json").read_text())
                self.assertEqual(outcome["exitCode"], 0)
                self.assertIn(outcome["cpuCore"], cores)
                self.assertEqual(seen["cpus"], [outcome["cpuCore"]])
                self.assertEqual(seen["omp"], PINNED_ENVIRONMENT["OMP_NUM_THREADS"])
                self.assertEqual(seen["dotnet"], "1")


if __name__ == "__main__":
    unittest.main()
