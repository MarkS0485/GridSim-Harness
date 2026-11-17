"""Render the live rehearsal comparison from complete case manifests."""
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

if len(sys.argv) < 2:
    sys.exit("Usage: python render_progress.py SUITE_OUTPUT_DIR [--watch]")
root = Path(sys.argv[1])
threshold = 1e-8
modes = ("dense", "sparse", "matpower", "pandapower", "pypsa", "veragrid",
         "powsybl", "andes", "powermodels")
_suite = root / "suite-manifest.json"
expected = (len(json.loads(_suite.read_text(encoding="utf-8"))["scheduledCases"])
            if _suite.is_file() else 67)


def render():
    with (root / "comparison.csv").open(newline="", encoding="ascii") as stream:
        groups = defaultdict(list)
        for row in csv.DictReader(stream):
            if row.get("case_exit_code") is not None:
                groups[row["case"]].append(row)
    groups = {name: rows for name, rows in groups.items() if len(rows) == len(modes)}
    def passes(row):
        return (row["converged"] == "10" and bool(row["max_original_residual_pu"])
                and float(row["max_original_residual_pu"]) <= threshold)

    labels = {"dense": "GridSim Dense", "sparse": "GridSim Sparse",
              "matpower": "MATPOWER", "pandapower": "pandapower",
              "pypsa": "PyPSA", "veragrid": "VeraGrid", "powsybl": "PowSyBl",
              "andes": "ANDES", "powermodels": "PowerModels"}

    def winners(rows, column):
        candidates = [(float(row[column]), row["path"]) for row in rows
                      if passes(row) and row[column]]
        if not candidates:
            return []
        best = min(value for value, _ in candidates)
        return [path for value, path in candidates if value == best]

    win_counts = {column: Counter(path for rows in groups.values()
                                  for path in winners(rows, column))
                  for column in ("max_original_residual_pu", "mean_ms")}

    by_mode = defaultdict(list)
    sparse_leads = 0
    sparse_ratios = []
    for rows in groups.values():
        for row in rows:
            by_mode[row["path"]].append(row)
        ranked = sorted((float(row["mean_ms"]), row["path"]) for row in rows
                        if passes(row) and row["mean_ms"])
        if ranked and ranked[0][1] == "sparse":
            sparse_leads += 1
            external = next((ms for ms, mode in ranked if mode not in ("dense", "sparse")), None)
            if external is not None:
                sparse_ratios.append(external / ranked[0][0])

    lines = [
        "# AC power-flow suite rehearsal",
        "",
        f"{len(groups)}/{expected} cases reported. Timing includes any system contention on the machine.",
        "",
        "| Solver path | Cases with 10/10 convergence | Cases with 10/10 and residual at or below 1e-8 pu |",
        "| --- | ---: | ---: |",
        *(f"| {mode} | {sum(row['converged'] == '10' for row in by_mode[mode])} | "
          f"{sum(passes(row) for row in by_mode[mode])} |" for mode in modes),
        "",
        f"Sparse was fastest among paths meeting both checks on {sparse_leads} cases.",
        *( [f"On {len(sparse_ratios)} of those cases an external path also met both checks. "
            f"The median ratio of the fastest external path to Sparse was {median(sparse_ratios):.2f}x."]
           if sparse_ratios else []),
        "",
        "## Win counts",
        "",
        "Ties count as a win for each tied path. Cases without an eligible path award no wins.",
        "",
        "| Solver path | Residual wins | Compute time wins |",
        "| --- | ---: | ---: |",
        *(f"| {labels[mode]} | {win_counts['max_original_residual_pu'][mode]} | "
          f"{win_counts['mean_ms'][mode]} |" for mode in modes),
        "",
        "## Cases",
        "",
        "Cells show mean solve ms, convergence count, then worst independent residual in pu.",
        "An asterisk marks missing residual evidence or a worst residual above 1e-8 pu.",
        "",
        "Winners require 10/10 convergence and a worst residual at or below 1e-8 pu. Ties use the unrounded values.",
        "",
        "| Case | Buses | " + " | ".join(modes) + " | Winner Residuals | Winner Compute Time |",
        "| --- | ---: | " + " | ".join(["---:"] * len(modes)) + " | --- | --- |",
    ]
    for name, rows in groups.items():
        manifest = root / Path(name).stem / "manifest.json"
        cells = []
        for row in rows:
            count = int(row["converged"])
            if row["mean_ms"]:
                residual = row["max_original_residual_pu"]
                flag = "*" if not residual or float(residual) > threshold else ""
                residual_text = f"{float(residual):.8e} pu" if residual else "no residual"
                cells.append(f"{float(row['mean_ms']):.4g} ({count}/10) {residual_text}{flag}")
            else:
                cells.append(f"{count}/10" if manifest.is_file() else "No evidence")
        buses = rows[0]["buses"]
        for column in win_counts:
            cells.append(", ".join(labels[path] for path in winners(rows, column)) or "-")
        lines.append(f"| {name} | {buses} | " + " | ".join(cells) + " |")
    lines.extend(("", "## Evidence notes", "", "| Case | Evidence notes |", "| --- | --- |"))
    lines.append("| Interpreting missing GridSim results | GridSim is designed to withhold a converged result when its solve checks or applicable constraints are not satisfied. An empty result alone does not establish Newton non-convergence or physical infeasibility. Check the recorded attempts and logs for the cause. The harness also audits saved GridSim solutions independently. |")
    for name, rows in groups.items():
        manifest = root / Path(name).stem / "manifest.json"
        if not manifest.is_file():
            lines.append(f"| {name} | No manifest. See the case log and materialise.log. |")
            continue
        evidence = json.loads(manifest.read_text(encoding="utf-8"))
        notes = []
        if name == "case16am.m":
            notes.append("GridSim Dense, GridSim Sparse and MATPOWER each hit 50 iterations on all ten attempts from the shared flat start. No path passed the common residual threshold under these settings")
        if evidence["changedHarnessFiles"]:
            notes.append("harness files changed during this case")
        cross = evidence["crossPathDifferences"]
        if cross is not None:
            notes.append(f"{len(cross)} Dense/Sparse bit differences on first converged results")
        for path in evidence["paths"]:
            bad = [item["referenceMismatchPu"] for item in path["attempts"]
                   if item.get("referenceMismatchPu", [0])[0] > threshold]
            if bad:
                value, bus, quantity = max(bad)
                notes.append(f"{path['mode']} worst residual {value:.17g} pu at bus {bus} ({quantity})")
            audit_errors = sum(audit["exitCode"] != 0 for audit in path.get("audits", []))
            if audit_errors:
                notes.append(f"{path['mode']} failed audits: {audit_errors}")
        if notes:
            lines.append(f"| {name} | " + "; ".join(notes) + ". |")
    result = "\n".join(lines) + "\n"
    target = root / "progress.md"
    target.write_text(result, encoding="ascii")
    return len(groups)


if "--watch" in sys.argv:
    while render() < expected:
        time.sleep(20)
else:
    print(f"{render()}/{expected} cases reported")
