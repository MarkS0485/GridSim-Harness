"""Run every pinned MATPOWER 8.1 case within a bus limit, smallest first, one case at a time.

Each case goes through run.py (--case), so every solver path, the --jobs/--cores/--pin
scheduler and the per-case evidence are exactly those of a single-case run. This script adds:

* the case list: matpower-8.1-cases.json pins each case's bus count and SHA-256; a case
  whose bytes differ from its pin is refused, and data/ cases that are not pinned are
  listed in suite-manifest.json and skipped;
* one Octave pass (materialise_cases.m) that flattens the cases for every path, and a
  label for the cases it cannot support (DC lines, switches, bus links);
* comparison.csv and attempt-results.csv across the whole suite, plus progress.md.

Timing runs on Linux: --jobs 1 --pin (one path at a time on one fixed CPU).
"""
import argparse
import csv
import hashlib
import json
import platform
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from run import CASE_CATALOGUE, CASE_DIR, CASES, case_file, digest, executable
from scheduler import host_metadata, is_linux

HERE = Path(__file__).resolve().parent
MODES = ("dense", "sparse", "gpu-dense", "gpu-sparse", "matpower", "pandapower", "pypsa", "veragrid",
         "powsybl", "andes", "powermodels")
HELPERS = ("run.py", "run-suite.py", "materialise_cases.m", "matpower-8.1-cases.json",
           "build_results.py", "build_chart_pack.py", "render_progress.py")
COLUMNS = ("case", "buses", "path", "status", "converged", "mean_ms",
           "max_original_residual_pu", "case_exit_code", "note")


def select_cases(matpower, max_buses, names=None, min_buses=0):
    """Return (selected [(buses, name)], unlisted data/ case files). Refuses altered pinned files.

    Cases marked pfEquivalentOf (extended catalogue: same power-flow data as another case) are not selected."""
    data = matpower / "data"
    if names:
        unknown = sorted(set(names) - CASES.keys())
        if unknown:
            raise ValueError(f"Not in {CASE_CATALOGUE.name}, so not pinned: {', '.join(unknown)}")
    chosen = sorted((entry["buses"], name) for name, entry in CASES.items()
                    if (names and name in names) or (not names and min_buses <= entry["buses"] <= max_buses
                                                     and "pfEquivalentOf" not in entry))
    if not chosen:
        raise ValueError("No pinned cases selected")
    for _, name in chosen:
        source = case_file(matpower, name)
        if not source.is_file():
            raise ValueError(f"Missing pinned input: {source}")
        if digest(source) != CASES[name]["sha256"]:
            raise ValueError(f"Case bytes do not match {CASE_CATALOGUE.name}: {source}")
    unlisted = [] if CASE_DIR else sorted(path.name for path in data.glob("case*.m") if path.name not in CASES)
    return chosen, unlisted


def unsupported_reasons(log_path):
    """Map case file name to the reason materialise_cases.m gave for rejecting it."""
    reasons = {}
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^(\S+\.m): (.+)$", line)
            if match:
                reasons[match.group(1)] = match.group(2)
    return reasons


def case_rows(name, buses, folder, materialised, reason, exit_code):
    """comparison.csv rows (one per solver path) for one case."""
    manifest = folder / "manifest.json"
    rows = {}
    if manifest.is_file():
        evidence = json.loads(manifest.read_text(encoding="utf-8"))
        rows = {row["mode"]: row for row in evidence["paths"]}
    table = []
    for mode in MODES:
        attempts = rows.get(mode, {}).get("attempts", [])
        converged = [a for a in attempts if a.get("converged")]
        durations = [a["elapsedMs"] for a in converged if a.get("elapsedMs") is not None]
        residuals = [a["referenceMismatchPu"][0] for a in converged
                     if a.get("referenceMismatchPu") is not None]
        if not (materialised / name).is_file():
            status, note = "case not materialised", reason or "no reason recorded"
        elif mode not in rows:
            status, note = "no evidence", ""
        elif len(converged) != 10 or len(durations) != 10 or len(residuals) != 10:
            status, note = "incomplete", ""
        else:
            status, note = "complete", ""
        table.append((name, buses, mode, status, len(converged),
                      statistics.mean(durations) if durations else "",
                      max(residuals) if residuals else "", exit_code, note))
    return table


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gridsim", type=Path, required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matpower", type=Path, default=HERE / "matpower-8.1")
    parser.add_argument("--external-python", type=Path, required=True)
    parser.add_argument("--octave", type=Path, required=True)
    parser.add_argument("--julia", type=Path, required=True)
    parser.add_argument("--julia-project", type=Path, default=HERE / "julia-env")
    parser.add_argument("--max-buses", type=int, default=5000,
                        help="Run pinned cases up to this many buses (default 5000: 67 cases)")
    parser.add_argument("--gpu", action="store_true", help="Also run the GridSim gpu-dense / gpu-sparse paths")
    parser.add_argument("--min-buses", type=int, default=0,
                        help="Smallest case to run (with --max-buses, selects a size band, e.g. the large tier)")
    parser.add_argument("--cases", nargs="+", metavar="CASE.m",
                        help="Run only these pinned cases (overrides --max-buses)")
    parser.add_argument("--only", nargs="+", choices=MODES,
                        help="Run selected paths only, for adapter development")
    parser.add_argument("--rehearsal", action="store_true", help="Label a noisy-system rehearsal")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Paths to run at once, each pinned to its own CPU (Linux). Default 1: "
                             "one path at a time. Several at once is not recommended for timing runs.")
    parser.add_argument("--cores", help="CPUs available for pinned jobs, e.g. 1-9 (Linux)")
    parser.add_argument("--reserve-cores", type=int, default=1,
                        help="Lowest-numbered CPUs left to the OS when --cores is not given")
    parser.add_argument("--pin", action="store_true",
                        help="Pin even with --jobs 1: one fixed CPU, OS left on the others (Linux). "
                             "Recommended for timing runs.")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if (args.jobs > 1 or args.pin or args.cores) and not is_linux():
        parser.error("--jobs above 1, --pin and --cores need Linux (taskset and sched_getaffinity)")
    return parser, args


def main(argv=None):
    parser, args = parse_arguments(argv)
    args.matpower = args.matpower.resolve()
    try:
        cases, unlisted = select_cases(args.matpower, args.max_buses, args.cases, args.min_buses)
    except ValueError as error:
        parser.error(str(error))
    runtime = []
    for name in ("gridsim", "driver", "matpower", "julia-project"):
        runtime += [f"--{name}", str(getattr(args, name.replace("-", "_")).resolve(strict=True))]
    for name in ("external-python", "octave", "julia"):
        # Not resolved: a venv's python is a symlink and must stay the venv's python.
        path = executable(getattr(args, name.replace("-", "_")))
        if not path.is_file():
            parser.error(f"--{name} does not exist: {path}")
        runtime += [f"--{name}", str(path)]
    scheduler = ["--jobs", str(args.jobs), "--reserve-cores", str(args.reserve_cores)]
    if args.pin:
        scheduler.append("--pin")
    if args.cores:
        scheduler += ["--cores", args.cores]
    if args.only:
        scheduler += ["--only", *args.only]
    if args.rehearsal:
        scheduler.append("--rehearsal")
    if args.gpu:
        scheduler.append("--gpu")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    materialised = output / "materialised"
    materialised.mkdir()
    suite_manifest = dict(
        startedUtc=datetime.now(timezone.utc).isoformat(),
        runLabel="Busy-machine rehearsal" if args.rehearsal else "Measurement run",
        platform=platform.platform(), host=host_metadata(), command=sys.argv,
        maxBuses=args.max_buses, minBuses=args.min_buses, catalogue=CASE_CATALOGUE.name,
        catalogueSha256=digest(CASE_CATALOGUE), caseDir=CASE_DIR, scheduledCases=[name for _, name in cases],
        caseSha256={name: CASES[name]["sha256"] for _, name in cases},
        unlistedDataCases=unlisted,
        harnessSourceSha256={file: hashlib.sha256((HERE / file).read_bytes()).hexdigest()
                             for file in HELPERS})
    (output / "suite-manifest.json").write_text(json.dumps(suite_manifest, indent=2) + "\n",
                                                encoding="utf-8")
    if unlisted:
        print(f"Skipping {len(unlisted)} unpinned data/ cases: {', '.join(unlisted)}", flush=True)

    with (output / "materialise.log").open("wb") as log:
        subprocess.run([str(executable(args.octave)), "--no-init-file", "--quiet",
                        str(HERE / "materialise_cases.m"), str(args.matpower),
                        str(materialised), *(name for _, name in cases)],
                       stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)
    reasons = unsupported_reasons(output / "materialise.log")

    failed = False
    with (output / "comparison.csv").open("w", newline="", encoding="ascii") as stream:
        table = csv.writer(stream)
        table.writerow(COLUMNS)
        for buses, name in cases:
            folder = output / Path(name).stem
            log_path = folder.with_suffix(".log")
            exit_code = ""
            if (materialised / name).is_file():
                print(f"{name}: {buses} buses", flush=True)
                command = [sys.executable, str(HERE / "run.py"), *runtime, "--case", name,
                           "--output", str(folder), "--materialised-dir", str(materialised),
                           *scheduler]
                with log_path.open("wb") as log:
                    exit_code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
                failed |= exit_code != 0
            else:
                reason = reasons.get(name, "no reason recorded")
                print(f"{name}: {buses} buses, unsupported ({reason})", flush=True)
                log_path.write_text(f"{name}: not run. {reason}\n", encoding="utf-8")
            table.writerows(case_rows(name, buses, folder, materialised, reasons.get(name),
                                      exit_code))
            stream.flush()

    for script, extra in (("build_results.py", ["--csv-only"]), ("render_progress.py", [])):
        status = subprocess.run([sys.executable, str(HERE / script), str(output), *extra])
        if status.returncode:
            print(f"Warning: {script} exited {status.returncode}", file=sys.stderr)
    print(f"Suite finished: {output}", flush=True)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
