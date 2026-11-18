"""Run the "models" tier: the GridSim JSON country models vendored in cases/models (GridSim-Models, ODbL 1.0).

Only GridSim reads these models, so there is no cross-tool comparison and no independent residual audit: the gate is
GridSim's own result (all 10 timed attempts converged, each with a final mismatch <= 1e-8 pu, and the 10 results
bit-identical). Each path runs through the same driver and scheduler as the MATPOWER suite (--jobs 1 --pin gives one
path at a time on one fixed CPU) and records time, iterations, mismatch and the worker's peak resident set.

Usage: python run-models.py --driver DLL --gridsim DIR --output DIR [--gpu] [--pin] [--models NAME ...]
"""
import argparse
import csv
import hashlib
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scheduler import host_metadata, run_command, select_cores

HERE = Path(__file__).resolve().parent
GATE_PU = 1e-8


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--gridsim", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", action="store_true", help="Also run gpu-sparse and gpu-dense")
    parser.add_argument("--dense-max-buses", type=int, default=6000)
    parser.add_argument("--pin", action="store_true", help="Pin each path to one fixed CPU (Linux)")
    parser.add_argument("--core", type=int, help="The CPU to pin to (default: the scheduler's first free CPU)")
    parser.add_argument("--models", nargs="+", help="Run only these models")
    args = parser.parse_args()
    catalogue = json.loads((HERE / "cases-extended.json").read_text(encoding="ascii"))["models"]
    names = sorted(args.models or catalogue, key=lambda n: (catalogue[n]["buses"], n))
    commit = subprocess.check_output(["git", "-C", str(args.gridsim), "rev-parse", "HEAD"], text=True).strip()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    core = (args.core if args.core is not None else select_cores(1, None, 1)[0]) if args.pin else None
    modes = ["sparse", "dense"] + (["gpu-sparse", "gpu-dense"] if args.gpu else [])
    (output / "models-manifest.json").write_text(json.dumps(dict(
        startedUtc=datetime.now(timezone.utc).isoformat(), host=host_metadata(), command=sys.argv,
        gridSimCommit=commit, cpuCore=core, modes=modes,
        catalogueSha256=hashlib.sha256((HERE / "cases-extended.json").read_bytes()).hexdigest()), indent=2) + "\n")
    columns = ("model", "buses", "path", "status", "converged", "iterations", "mean_ms", "min_ms",
               "max_mismatch_pu", "repeat_identical", "peak_rss_mb", "gpu_fallback_steps", "note")
    with (output / "models-comparison.csv").open("w", newline="", encoding="ascii") as stream:
        table = csv.writer(stream)
        table.writerow(columns)
        for name in names:
            entry = catalogue[name]
            model_dir = HERE / "cases" / "models" / name
            for mode in modes:
                if "dense" in mode and entry["buses"] > args.dense_max_buses:
                    table.writerow((name, entry["buses"], mode, "skipped", "", "", "", "", "", "", "", "",
                                    f"above --dense-max-buses {args.dense_max_buses}"))
                    continue
                folder = output / name / mode
                folder.parent.mkdir(exist_ok=True)
                command = ["dotnet", str(args.driver), str(model_dir), mode, str(folder), commit]
                folder.mkdir()
                outcome = run_command(command, folder / "driver.log", core)
                attempts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("attempt-*.json"))]
                results = [a["result"] for a in attempts]
                converged = sum(r["converged"] for r in results)
                mismatch = max((r["maxMismatch"] for r in results), default=None)
                times = [a["elapsedMs"] for a in attempts]
                keys = {json.dumps([r["vm"], r["vaDeg"]]) for r in results}
                fallback = sum((a.get("gpuSolves") or {}).get("gaveUpToCpu", 0) + (a.get("gpuSolves") or {}).get("onHost", 0)
                               for a in attempts)
                total = sum((a.get("gpuSolves") or {}).get("total", 0) for a in attempts)
                passed = (len(results) == 10 and converged == 10 and mismatch is not None and mismatch <= GATE_PU
                          and len(keys) == 1)
                status = "pass" if passed else ("error" if not results else "FAIL")
                note = outcome.get("error", "") or ("" if outcome.get("exitCode") == 0 else f"exit {outcome.get('exitCode')}")
                table.writerow((name, entry["buses"], mode, status, f"{converged}/{len(results)}",
                                results[0]["iterations"] if results else "",
                                f"{statistics.mean(times):.3f}" if times else "", f"{min(times):.3f}" if times else "",
                                f"{mismatch:.3e}" if mismatch is not None else "", len(keys) == 1 if results else "",
                                outcome.get("peakRssMb", ""), f"{fallback}/{total}" if total else "", note))
                stream.flush()
                print(f"{name} ({entry['buses']} buses) {mode}: {status}", flush=True)
    print(f"Models finished: {output}", flush=True)


if __name__ == "__main__":
    main()
