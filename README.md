# GridSim AC power-flow benchmark harness

This harness times GridSim's dense and sparse AC power-flow solvers. It
runs them against MATPOWER, pandapower, PyPSA, VeraGrid, PowSyBl Open Load
Flow, ANDES and PowerModels.jl on the same MATPOWER cases (IEEE 118-bus
smoke case and PEGASE 1,354-bus). It checks every result electrically
against the case independently. TOOLS.md lists each solver, its source
and its licence. Third-party runtimes and case data are not included.
Download them as described below.

Clone this repository beside a GridSim checkout (`../GridSim`). Build the
driver against that checkout:

    dotnet build driver/BenchmarkDriver.csproj -c Debug -nr:false -p:GridSimRoot=../GridSim -p:RestoreLockedMode=true

Use a new output directory for each invocation:

    python run.py --gridsim ../GridSim --driver driver/bin/Debug/net10.0/BenchmarkDriver.dll --output ./new-smoke --smoke

Omit --smoke for PEGASE. Each output folder holds JSON for the ten
attempts per path, their exports, the independent audit of each first
successful export, the manifest and the report. Output folders are not
committed.

## External adapter development

The harness verifies the byte hashes of the original MATPOWER 8.1 files
under the directory supplied with --matpower. It retains a derived .m
input with a common voltage start and explicit unity taps on shifted branches.
This gives all tools the same zero-angle start while preserving the two
original cases and their provenance.

Pass --external-python with the isolated Python executable for the five
Python solvers. Pass --octave with the GNU Octave CLI executable and
--julia with the Julia executable to add MATPOWER and PowerModels.
The Julia project defaults to julia-env beside the harness. These paths
are under development until the full smoke suite passes. Inspect each
worker.log and the manifest for setup errors and missing attempts.

## Full-roster commands

The development run uses the pinned prerequisite paths checked by the launcher.

    pwsh -NoProfile -File ./run-benchmark.ps1 -Smoke -Output ./smoke-roster
    pwsh -NoProfile -File ./run-benchmark.ps1 -Rehearsal -Output ./rehearsal-roster
    python ./verify_bundle.py ./rehearsal-roster

The rehearsal timings are retained for checking the run machinery. The public
report generator accepts only the separate PEGASE measurement bundle. Run a
measurement by omitting -Smoke and -Rehearsal and providing a fresh output path.

## Runtime preparation on Windows

Use Python 3.12 and the .NET SDK pinned by GridSim. In the runtime directory,
create the Python environment from the resolved lock:

    python -m venv .venv
    ./.venv/Scripts/python.exe -m pip install --no-compile -r requirements-windows-py312.lock
    ./.venv/Scripts/python.exe -m pip check

Provide MATPOWER 8.1 under matpower-8.1, GNU Octave 11.3.0 under
octave-11.3.0-w64, and Julia 1.10.12 under julia-1.10.12. Source links are
in TOOLS.md. Use -Octave if your CLI executable has a different path.
Set JULIA_DEPOT_PATH to the .julia directory under your runtime directory
and instantiate the pinned Julia project with:

    ./julia-1.10.12/bin/julia.exe --startup-file=no --project=julia-env -e 'using Pkg; Pkg.instantiate()'

The build command above prepares the scoped Debug GridSim driver. The
manifest records that configuration and the GridSim Core binary hash.
Run the smoke command after installing the runtimes to warm their imports
and check that all nine paths can report attempts.

## Runtime preparation on Linux (Ubuntu)

Clone this repository and GridSim side by side, for example
`~/bench/GridSim-Harness` and `~/bench/GridSim`. The manifest records
the HEAD commit of the GridSim checkout passed with --gridsim, so build the
driver from that same checkout.

System packages (the apt .NET packages also install libicu, which the
driver needs; on Ubuntu 24.04 the .NET 10 SDK comes from
`ppa:dotnet/backports` or Microsoft's package feed):

    sudo apt install dotnet-sdk-10.0 octave util-linux git unzip curl

Python 3.12 is required by the lock. It is the system Python on Ubuntu
24.04; on other releases install a 3.12 interpreter first. In the
GridSim-Harness directory:

    python3.12 -m venv .venv
    ./.venv/bin/python -m pip install --no-compile -r requirements-linux-py312.lock
    ./.venv/bin/python -m pip check

requirements-linux-py312.lock pins the same versions as the Windows lock.
It was resolved for CPython 3.12 manylinux wheels and adds only pexpect and
ptyprocess, which IPython needs on Linux. VeraGridEngine ships only as a
source archive, so pip builds it locally.

MATPOWER, Julia and the Julia project (download links in TOOLS.md):

    unzip matpower8.1.zip && mv matpower8.1 matpower-8.1   # the zip unpacks to matpower8.1/
    curl -LO https://julialang-s3.julialang.org/bin/linux/x64/1.10/julia-1.10.12-linux-x86_64.tar.gz
    tar -xzf julia-1.10.12-linux-x86_64.tar.gz # gives julia-1.10.12/
    JULIA_DEPOT_PATH=$PWD/.julia ./julia-1.10.12/bin/julia --startup-file=no --project=julia-env -e 'using Pkg; Pkg.instantiate()'

Check the Julia download against its published SHA-256 (there is no
checksum file beside the tarball; it is under bin/checksums/):

    curl -s https://julialang-s3.julialang.org/bin/checksums/julia-1.10.12.sha256 | grep linux-x86_64.tar.gz
    sha256sum julia-1.10.12-linux-x86_64.tar.gz

The julia-env Manifest is platform-independent; Pkg fetches Linux artifacts.

GNU Octave: the launcher uses `octave-11.3.0/bin/octave-cli` under the
runtime directory if present, otherwise `octave-cli` on PATH. Ubuntu's
packaged Octave is older than the 11.3.0 used on Windows. Each MATPOWER
attempt records the Octave version. To match Windows, build 11.3.0 from
source with `--prefix=<runtime>/octave-11.3.0`, or pass --octave.

Build the driver as Release (no shared build lock is needed on the server):

    bash build-driver.sh --gridsim ../GridSim

It runs the locked restore first. If the Ubuntu SDK's ILLink task differs
from GridSim.Core's packages.lock.json the restore fails with NU1004; the
script then retries unlocked and restores the lock file so the GridSim
checkout stays clean. `run-benchmark.sh` uses `driver/bin/Release` when it
exists and falls back to Debug. The manifest records the build configuration.

Use a venv whose interpreter is not resolved to the system Python. run.py
and run-suite.py keep the path you give (`--python`, default
`.venv/bin/python`) as it is, so a plain symlinked venv works.

Smoke, then measure (one path at a time on one fixed CPU, the OS on the others):

    bash run-benchmark.sh --smoke --jobs 1 --pin --output ./smoke-linux
    ./.venv/bin/python -m unittest test_pilot test_scheduler test_suite
    bash run-benchmark.sh --jobs 1 --pin --output ./measurement-linux-$(date -u +%Y%m%d-%H%M%S)
    ./.venv/bin/python verify_bundle.py ./measurement-linux-...

`--jobs N` stays available but is not recommended for timing runs; see the
parallel section below.

test_pilot reads `smoke/dense/attempt-01.json`, so copy or link a smoke
bundle to `smoke` first.

## Full MATPOWER suite

`run-suite.py` runs every pinned MATPOWER 8.1 case up to 5,000 buses (67
cases, 4 to 3,374 buses), smallest first, each through the nine solver
paths. On Linux, for timing:

    bash build-driver.sh --gridsim ../GridSim
    bash run-benchmark.sh --suite --jobs 1 --pin --output ./suite-linux-$(date -u +%Y%m%d-%H%M%S)

That is `--jobs 1 --pin`: paths run one after another on one fixed CPU and
the OS keeps the others. It takes many hours. Add `--max-buses N` or
`--cases case14.m case118.m` for a subset, and `--rehearsal` to label a run
made on a busy machine. The same flags work directly:
`python run-suite.py --gridsim ../GridSim --driver driver/bin/Release/net10.0/BenchmarkDriver.dll
--output DIR --external-python .venv/bin/python --octave $(command -v octave-cli)
--julia julia-1.10.12/bin/julia --jobs 1 --pin`.

`matpower-8.1-cases.json` pins the bus count and SHA-256 of 78 MATPOWER 8.1
`data/` cases. A pinned case whose bytes differ is refused. Other
`data/case*.m` files are not pinned: they are listed under `unlistedDataCases`
in `suite-manifest.json` and skipped. `materialise_cases.m` (Octave) flattens
each case to bus, gen and branch matrices for every path and rejects cases
with DC lines, switches or bus links (`case_RTS_GMLC.m`). Those rows read
`case not materialised` in `comparison.csv`, with the reason in `note`.

Output folder:

- `<case>/` (`manifest.json`, `report.md`, per-path evidence) and `<case>.log`
- `comparison.csv`: one row per case and path (status, converged, mean ms,
  worst residual, case exit code)
- `attempt-results.csv`: one row per attempt
- `suite-manifest.json`, `materialise.log`, `materialised/`, `progress.md`

`build_results.py DIR` rebuilds `attempt-results.csv` and, with matplotlib,
three charts and the coverage table; `build_chart_pack.py DIR` writes the
ten-chart pack (matplotlib and numpy are not in the lock files);
`render_progress.py DIR [--watch]` refreshes `progress.md` while a run is going.

A single case runs with `run.py --case case14.m ...` (needs --octave).
case118 (`--smoke`) and case1354pegase behave as before.

## Parallel runs (Linux)

Each solver path already makes its ten attempts inside one worker process
after a warm-up. The parallel unit is therefore a path: with --jobs N, up
to N of the nine paths run at once. Each runs under `taskset -c <cpu>` on a
CPU no other running path holds. OMP, MKL, OpenBLAS, NumExpr, Numba and
Julia get one thread each. .NET uses `DOTNET_PROCESSOR_COUNT=1` and
workstation GC. CPUs are chosen one per physical core before SMT siblings.
The lowest CPU is left to the OS (--reserve-cores, default 1).

- `--jobs 1` (default) runs paths one after another as before, without
  pinning or environment changes. This is the Windows behaviour, and
  --jobs above 1 is refused there.
- `--pin` pins a --jobs 1 run to one fixed CPU.
- `--cores 1-9` restricts the CPUs. Run several cases or repeats at once
  by giving each launch a disjoint list, for example PEGASE on `1-9` and
  the smoke case on `10-18`.

The manifest records `scheduler` (jobs, CPUs, job environment) and
`host` (CPU model, kernel, OS, governors, cgroup quota, virtualisation,
load average). Each path row records `cpuCore` and `wallSeconds`.
report.md states how the paths were scheduled.

### Timing caveats

- A rented VM is noisy. Its vCPUs share physical cores, caches and memory
  bandwidth with other tenants, and the hypervisor can migrate them.
  Compare timings only within one machine and one run. Never compare them
  with Windows laptop timings or with a run on another day or instance.
- Concurrent paths share the last-level cache and memory bandwidth even
  when pinned. Use `--jobs 1 --pin` for timing runs. Higher --jobs is for
  quick passes, not for numbers you publish.
- Many VMs have no cpufreq (cpuGovernors is then null), so the host
  controls frequency scaling. Where cpufreq is available, set the
  performance governor before measuring.

## Licence

Copyright (C) 2026 Mark Shirley (MarkS0485).

This harness is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. See [LICENSE](LICENSE).

The licence covers this harness only. GridSim keeps its own licence (see the
GridSim repository). Each solver and dataset listed in TOOLS.md keeps its own
licence, and none of them is included here.
