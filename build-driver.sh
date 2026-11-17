#!/usr/bin/env bash
# Build the benchmark driver (default Release) against a GridSim checkout.
#
# Tries the locked restore first, as the Windows build does. On a Linux SDK whose
# bundled ILLink task differs from GridSim.Core's packages.lock.json the locked
# restore fails with NU1004; this retries unlocked and then restores any lock file
# the unlocked restore rewrote, so the GridSim checkout is left clean.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
config=Release gridsim=''
while (($#)); do
    case $1 in
        --configuration) config=$2; shift 2 ;;
        --gridsim) gridsim=$2; shift 2 ;;
        -h|--help) echo "Usage: build-driver.sh [--gridsim DIR] [--configuration Release|Debug]"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [[ -z $gridsim ]]; then
    if [[ -f $here/../../src/GridSim.Core/GridSim.Core.csproj ]]; then gridsim=$here/../..
    else gridsim=$here/../GridSim; fi
fi
gridsim=$(cd "$gridsim" && pwd)
build=(dotnet build "$here/driver/BenchmarkDriver.csproj" -c "$config" -nr:false "-p:GridSimRoot=$gridsim")

log=$(mktemp)
trap 'rm -f "$log"' EXIT
if ! "${build[@]}" -p:RestoreLockedMode=true 2>&1 | tee "$log"; then
    grep -q 'NU1004' "$log" || { echo "Driver build failed" >&2; exit 1; }
    echo "Locked restore hit NU1004 (SDK package versions differ from the lock); retrying unlocked." >&2
    "${build[@]}" -p:RestoreLockedMode=false
    git -C "$gridsim" checkout -- $(git -C "$gridsim" ls-files -m -- '*packages.lock.json') 2>/dev/null || true
fi
echo "Driver: $here/driver/bin/$config/net10.0/BenchmarkDriver.dll"
