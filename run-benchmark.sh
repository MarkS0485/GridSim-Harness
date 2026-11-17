#!/usr/bin/env bash
# Linux launcher for the full roster. Mirrors run-benchmark.ps1 and adds --jobs/--cores.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: run-benchmark.sh --output DIR [--smoke | --rehearsal] [--jobs N] [--cores LIST] [--pin]
                        [--gridsim DIR] [--runtime-root DIR] [--python PATH] [--octave PATH]
                        [--julia PATH] [--driver PATH]
       run-benchmark.sh --suite --output DIR [--max-buses N] [--cases CASE.m ...] [same options]

--suite      run every pinned MATPOWER 8.1 case (run-suite.py), not just one case

--jobs N     run up to N paths at once, each pinned to its own CPU (default 1: one after another)
--cores LIST CPUs the pinned paths may use, e.g. 1-9; give disjoint lists to concurrent launches
--pin        pin a --jobs 1 run to a single fixed CPU. Recommended for timing runs:
             --jobs 1 --pin runs one path at a time on one fixed CPU, the OS on the others
The driver is driver/bin/Release/... if built (build-driver.sh), otherwise Debug.
EOF
}

here=$(cd "$(dirname "$0")" && pwd)
output='' gridsim='' runtime=$here python='' octave='' julia='' driver=''
extra=() suite=0
while (($#)); do
    case $1 in
        --output) output=$2; shift 2 ;;
        --smoke) extra+=(--smoke); shift ;;
        --suite) suite=1; shift ;;
        --max-buses) extra+=(--max-buses "$2"); shift 2 ;;
        --cases) shift; extra+=(--cases); while (($#)) && [[ $1 != --* ]]; do extra+=("$1"); shift; done ;;
        --rehearsal) extra+=(--rehearsal); shift ;;
        --jobs) extra+=(--jobs "$2"); shift 2 ;;
        --cores) extra+=(--cores "$2"); shift 2 ;;
        --pin) extra+=(--pin); shift ;;
        --gridsim) gridsim=$2; shift 2 ;;
        --runtime-root) runtime=$2; shift 2 ;;
        --python) python=$2; shift 2 ;;
        --octave) octave=$2; shift 2 ;;
        --julia) julia=$2; shift 2 ;;
        --driver) driver=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done
[[ -n $output ]] || { usage >&2; exit 2; }

if [[ -z $gridsim ]]; then
    if [[ -f $here/../../src/GridSim.Core/GridSim.Core.csproj ]]; then
        gridsim=$here/../..
    else
        gridsim=$here/../GridSim
    fi
fi
runtime=$(cd "$runtime" && pwd)
gridsim=$(cd "$gridsim" && pwd)
python=${python:-$runtime/.venv/bin/python}
if [[ -z $octave ]]; then
    if [[ -x $runtime/octave-11.3.0/bin/octave-cli ]]; then
        octave=$runtime/octave-11.3.0/bin/octave-cli
    else
        octave=$(command -v octave-cli || true)
    fi
fi
julia=${julia:-$runtime/julia-1.10.12/bin/julia}
if [[ -z $driver ]]; then
    # Prefer a Release build (see build-driver.sh); fall back to Debug.
    driver=$runtime/driver/bin/Release/net10.0/BenchmarkDriver.dll
    [[ -f $driver ]] || driver=$runtime/driver/bin/Debug/net10.0/BenchmarkDriver.dll
fi
project=$runtime/julia-env
# The MATPOWER release zip unpacks to matpower8.1/; the harness expects matpower-8.1/.
matpower=$runtime/matpower-8.1
[[ -d $matpower || ! -d $runtime/matpower8.1 ]] || matpower=$runtime/matpower8.1

for path in "$python" "$octave" "$julia" "$project/Manifest.toml" "$matpower/lib/runpf.m" "$driver"; do
    [[ -n $path && -f $path ]] || { echo "Benchmark prerequisite missing: ${path:-octave-cli}" >&2; exit 1; }
done
command -v dotnet >/dev/null || { echo "Benchmark prerequisite missing: dotnet" >&2; exit 1; }
command -v taskset >/dev/null || { echo "Benchmark prerequisite missing: taskset (util-linux)" >&2; exit 1; }

export JULIA_DEPOT_PATH=$runtime/.julia
export JULIA_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1

entry=run.py
((suite)) && entry=run-suite.py
exec "$python" "$here/$entry" --gridsim "$gridsim" --driver "$driver" --output "$output" \
    --matpower "$matpower" --external-python "$python" --octave "$octave" \
    --julia "$julia" --julia-project "$project" "${extra[@]}"
