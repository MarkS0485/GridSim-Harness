"""Run benchmark worker processes, sequentially or in parallel with one pinned CPU each."""
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TIMEOUT_SECONDS = 900

# Applied to every pinned job so that no runtime starts more threads than its single core.
PINNED_ENVIRONMENT = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMBA_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "JULIA_NUM_THREADS": "1",
    "DOTNET_PROCESSOR_COUNT": "1",
    "DOTNET_gcServer": "0",
    "DOTNET_GCHeapCount": "1",
}

SYSFS_CPU = Path("/sys/devices/system/cpu")


def is_linux():
    return platform.system() == "Linux" and hasattr(os, "sched_getaffinity")


def parse_cores(text):
    """Parse a taskset-style list such as '1-8,10,12-13' into sorted unique CPU ids."""
    cores = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        if start < 0 or end < start:
            raise ValueError(f"Invalid CPU range: {part}")
        cores.update(range(start, end + 1))
    if not cores:
        raise ValueError("Empty CPU list")
    return sorted(cores)


def _read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def physical_order(cores, sysfs=SYSFS_CPU):
    """Order CPUs so that one logical CPU per physical core comes before any SMT sibling."""
    seen = set()
    first, siblings = [], []
    for cpu in cores:
        package = _read(sysfs / f"cpu{cpu}/topology/physical_package_id")
        core = _read(sysfs / f"cpu{cpu}/topology/core_id")
        key = (package, core) if package is not None and core is not None else ("cpu", cpu)
        (siblings if key in seen else first).append(cpu)
        seen.add(key)
    return first + siblings


def select_cores(count, requested=None, reserve=1):
    """Return `count` CPU ids for pinned jobs, leaving the lowest `reserve` CPUs to the OS."""
    allowed = sorted(os.sched_getaffinity(0))
    if requested is not None:
        outside = sorted(set(requested) - set(allowed))
        if outside:
            raise ValueError(f"CPUs {outside} are outside this process's affinity {allowed}")
        candidates = list(requested)
    else:
        candidates = allowed[reserve:]
    candidates = physical_order(candidates)
    if len(candidates) < count:
        raise ValueError(f"{count} parallel jobs need {count} CPUs; only {candidates} are available")
    return candidates[:count]


class Job:
    def __init__(self, name, command, folder, log_name):
        self.name = name
        self.command = command
        self.folder = folder
        self.log_name = log_name


def run_command(command, log_path, core=None, env=None):
    """Run one worker with its output in log_path; return the outcome fields for its row."""
    launch = command if core is None else ["taskset", "-c", str(core), *command]
    outcome = {}
    if core is not None:
        outcome["cpuCore"] = core
    started = time.perf_counter()
    with log_path.open("wb") as log:
        try:
            status = subprocess.run(launch, stdout=log, stderr=subprocess.STDOUT, env=env,
                                    timeout=TIMEOUT_SECONDS, check=False)
            outcome["exitCode"] = status.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            outcome["error"] = str(error)
    outcome["wallSeconds"] = round(time.perf_counter() - started, 3)
    return outcome


def run_jobs(jobs, cores=None, log=print):
    """Run every job and return {name: outcome}.

    Without cores the jobs run one after another with the inherited environment, exactly
    as the sequential harness always did. With cores, up to len(cores) jobs run at once,
    each under taskset on a CPU no other running job holds, with PINNED_ENVIRONMENT set.
    """
    if not cores:
        outcomes = {}
        for job in jobs:
            log(f"Running {job.name}", flush=True)
            outcomes[job.name] = run_command(job.command, job.folder / job.log_name)
        return outcomes
    if shutil.which("taskset") is None:
        raise RuntimeError("Pinned runs need taskset (util-linux)")
    env = {**os.environ, **PINNED_ENVIRONMENT}
    free = queue.Queue()
    for core in cores:
        free.put(core)
    lock = threading.Lock()

    def run(job):
        core = free.get()
        try:
            with lock:
                log(f"Started {job.name} on CPU {core}", flush=True)
            outcome = run_command(job.command, job.folder / job.log_name, core, env)
            with lock:
                status = outcome.get("exitCode", outcome.get("error"))
                log(f"Finished {job.name} on CPU {core}: {status} after {outcome['wallSeconds']} s",
                    flush=True)
            return outcome
        finally:
            free.put(core)

    with ThreadPoolExecutor(max_workers=len(cores)) as pool:
        futures = {job.name: pool.submit(run, job) for job in jobs}
        return {name: future.result() for name, future in futures.items()}


def host_metadata():
    """Describe the machine so timings are only compared within one machine and run."""
    info = {"system": platform.system(), "release": platform.release(),
            "machine": platform.machine()}
    if not is_linux():
        return info
    cpuinfo = _read("/proc/cpuinfo") or ""
    models = Counter(line.split(":", 1)[1].strip() for line in cpuinfo.splitlines()
                     if line.startswith("model name"))
    flags = next((line.split(":", 1)[1].split() for line in cpuinfo.splitlines()
                  if line.startswith("flags")), [])
    info["cpuModels"] = dict(models)
    info["hypervisorFlag"] = "hypervisor" in flags
    info["kernel"] = _read("/proc/version")
    os_release = _read("/etc/os-release") or ""
    info["osRelease"] = next((line.split("=", 1)[1].strip('"') for line in os_release.splitlines()
                              if line.startswith("PRETTY_NAME=")), None)
    meminfo = _read("/proc/meminfo") or ""
    info["memTotal"] = next((line.split(":", 1)[1].strip() for line in meminfo.splitlines()
                             if line.startswith("MemTotal")), None)
    info["allowedCpus"] = sorted(os.sched_getaffinity(0))
    governors = Counter(filter(None, (_read(path) for path in
                                      SYSFS_CPU.glob("cpu[0-9]*/cpufreq/scaling_governor"))))
    info["cpuGovernors"] = dict(governors) or None
    info["cpuFreqDriver"] = _read(SYSFS_CPU / "cpu0/cpufreq/scaling_driver")
    info["smtActive"] = _read(SYSFS_CPU / "smt/active")
    info["cgroupCpuMax"] = _read("/sys/fs/cgroup/cpu.max")
    if shutil.which("systemd-detect-virt"):
        result = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True,
                                check=False)
        info["virtualization"] = result.stdout.strip() or None
    info["loadAverage"] = list(os.getloadavg())
    return info
