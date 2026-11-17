"""Collect a run-suite.py output folder into attempt-results.csv and summary charts.

Usage: python build_results.py SUITE_OUTPUT_DIR [--csv-only]

run-suite.py runs the --csv-only step itself. The charts need matplotlib.
"""
import csv
import json
import statistics
import sys
from pathlib import Path
from collections import defaultdict

from case_reference import load_case, calculate, controlled_voltage_error
from run import CASES

if len(sys.argv) < 2:
    sys.exit(__doc__)
root = Path(sys.argv[1]).resolve()
csv_only = "--csv-only" in sys.argv
suite = (json.loads((root / "suite-manifest.json").read_text(encoding="utf-8"))
         if (root / "suite-manifest.json").is_file() else {})
run_label = suite.get("runLabel", "Busy-machine rehearsal")
modes = "dense sparse matpower pandapower pypsa veragrid powsybl andes powermodels".split()
labels = dict(zip(modes, ["GridSim Dense","GridSim Sparse","MATPOWER","pandapower","PyPSA","VeraGrid","PowSyBl","ANDES","PowerModels"]))
colours = dict(zip(modes, ["#5B6579","#007E87","#C76227","#AD3277","#5544B9","#7B7620","#2874C6","#B43C3C","#268349"]))
scheduled = suite.get("scheduledCases") or [k for k, v in CASES.items() if v["buses"] <= 5000]
cases = sorted((CASES[k]["buses"], k) for k in scheduled)
by_path = defaultdict(list)
attempts = []
# A case folder with saved attempts but no manifest is a run that was stopped part-way.
partial = next((case for _, case in cases
                if not (root / Path(case).stem / "manifest.json").exists()
                and any((root / Path(case).stem).glob("*/attempt-*.json"))), None)
for buses, case in cases:
    directory = root / Path(case).stem
    manifest = directory / "manifest.json"
    if manifest.exists():
        records = json.loads(manifest.read_text(encoding="utf-8"))["paths"]
    elif case == partial:
        reference = load_case(root / "materialised" / case)
        records = []
        for mode in modes:
            rows = []
            for file in sorted((directory / mode).glob("attempt-*.json")):
                raw = json.loads(file.read_text(encoding="utf-8"))
                result = raw.get("result", raw)
                row = dict(attempt=raw["attempt"], elapsedMs=raw["elapsedMs"],
                           converged=result["converged"], iterations=result.get("iterations"))
                if result["converged"]:
                    voltage = (dict(zip(result["busIds"], zip(result["vm"], result["vaDeg"])))
                               if "result" in raw else {int(i):(v,a) for i,v,a in raw["bus"]})
                    try:
                        row["referenceMismatchPu"] = calculate(reference, voltage)[1]
                        row["referenceVoltageTargetErrorPu"] = controlled_voltage_error(reference, voltage)
                    except (ArithmeticError, ValueError) as error:
                        row["comparisonError"] = str(error)
                rows.append(row)
            records.append(dict(mode=mode, attempts=rows))
    else:
        continue
    for record in records:
        mode = record["mode"]
        for raw in record["attempts"]:
            item = dict(case=case, buses=buses, path=mode, caseEvidence="partial" if case == partial else "reported",
                        attempt=raw["attempt"], status=raw.get("status","recorded"),
                        elapsedMs=raw.get("elapsedMs"), converged=raw.get("converged"),
                        iterations=raw.get("iterations"), workerExitCode=record.get("exitCode"),
                        error=raw.get("comparisonError") or raw.get("error") or record.get("error"))
            residual = raw.get("referenceMismatchPu")
            if residual:
                item.update(zip(("residualPu","residualBus","equation"),residual))
            target = raw.get("referenceVoltageTargetErrorPu")
            if target:
                item.update(zip(("voltageTargetErrorPu","voltageTargetBus"),target))
            attempts.append(item)
            by_path[case,mode].append(item)

with (root/"attempt-results.csv").open("w",newline="",encoding="ascii",errors="backslashreplace") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for a in attempts for k in a)))
    writer.writeheader()
    writer.writerows(attempts)

if csv_only:
    print(f"{len(attempts)} attempt records written to {root / 'attempt-results.csv'}")
    sys.exit(0)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D


def summarise(rows):
    good = [r for r in rows if r["converged"] is True]
    times = [r["elapsedMs"] for r in good if r["elapsedMs"] is not None]
    residuals = [r["residualPu"] for r in good if "residualPu" in r]
    recorded = sum(r["converged"] is not None for r in rows)
    return recorded,len(good),statistics.mean(times) if times else None,max(residuals,default=None),len(residuals)

summary = {(case,mode):summarise(by_path[case,mode]) for _,case in cases for mode in modes}
plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"text.color":"#182941","axes.labelcolor":"#182941",
                     "axes.edgecolor":"#D3DCE6","xtick.color":"#465870","ytick.color":"#465870","figure.facecolor":"#F3F7FB"})
out = root/"charts"
out.mkdir(exist_ok=True)

def save(fig,name,title,subtitle):
    fig.text(.06,.96,title,fontsize=23,fontweight="bold",va="top")
    fig.text(.06,.91,subtitle,fontsize=11,va="top",color="#465870")
    for ext in ("png","svg"):
        fig.savefig(out/f"{name}.{ext}",dpi=160,facecolor=fig.get_facecolor())
    plt.close(fig)

for name,idx,ylabel,title in [
    ("solve-time",2,"Mean solve time (ms, log scale)","Solve time across network sizes"),
    ("residuals",3,"Worst common-case residual (pu, log scale)","Electrical agreement across network sizes")]:
    fig,ax=plt.subplots(figsize=(13,7))
    fig.subplots_adjust(left=.09,right=.96,bottom=.19,top=.85)
    ax.set(xscale="log",yscale="log",xlabel="Buses (log scale)",ylabel=ylabel)
    ax.grid(True,which="major",color="#DEE6EF")
    for mode in modes:
        for is_partial in (False,True):
            points = [(n,summary[c,mode][idx], i) for i,(n,c) in enumerate(cases)
                      if (c == partial) == is_partial and summary[c,mode][idx] is not None]
            if points:
                ax.scatter([p[0] for p in points],[max(p[1],1e-17) for p in points],
                           s=42 if mode=="sparse" or is_partial else 24,
                           marker="D" if is_partial else "o",color=colours[mode],alpha=.83,
                           label=labels[mode] if not is_partial else None)
    if idx==3:
        ax.axhline(1e-8,color="#263852",ls="--",lw=1)
        ax.annotate("Common threshold: 1e-8 pu",(5,1e-8),xytext=(0,6),textcoords="offset points")
    handles, names = ax.get_legend_handles_labels()
    handles.append(Line2D([],[],marker="D",linestyle="",color="#465870"))
    names.append("Interrupted case")
    ax.legend(handles,names,loc="upper center",bbox_to_anchor=(.5,-.13),ncol=5,frameon=False,fontsize=9)
    save(fig,name,title,f"{run_label}  /  One point per case and solver with a saved converged result")

fig,ax=plt.subplots(figsize=(17,7))
fig.subplots_adjust(left=.13,right=.98,bottom=.34,top=.85)
palette=["#E1E7EF","#F29584","#F7D27C","#78C5B5","#A7B6DC"]
statuses=[]
for mode in modes:
    row=[]
    for _,case in cases:
        recorded,good,_,res,count=summary[case,mode]
        row.append(4 if case==partial and recorded else 0 if not recorded else
                   1 if not good else 3 if good==10 and count==10 and res<=1e-8 else 2)
    statuses.append(row)
ax.imshow(statuses,aspect="auto",cmap=ListedColormap(palette),vmin=-.5,vmax=4.5)
ax.set_xticks(range(len(cases)),[c.removeprefix("case").removesuffix(".m") for _,c in cases],rotation=90,fontsize=8)
ax.set_yticks(range(len(modes)),[labels[m] for m in modes])
ax.tick_params(length=0)
legend=["No attempts","0 converged","Converged, check incomplete or failed","10/10 within 1e-8 pu","Evidence from interrupted case"]
ax.legend([Line2D([],[],marker="s",linestyle="",color=c,markersize=9) for c in palette],legend,
          loc="upper center",bbox_to_anchor=(.5,-.57),ncol=5,frameon=False,fontsize=9)
save(fig,"coverage","What each solver actually returned",f"All {len(cases)} scheduled cases, ordered by bus count  /  Status is based on saved attempts")

lines=["","## Charts and saved evidence","",
       "The timing and residual charts include the saved results from the interrupted case. Diamonds identify those results. "
       f"Timings are means of converged attempts. Run: {run_label.lower()}.",""]
for chart,label in [("solve-time","Solve time by bus count"),("residuals","Worst residual by bus count"),("coverage","Solver coverage across all scheduled cases")]:
    lines += [f"![{label}](charts/{chart}.png)",f"[Vector chart](charts/{chart}.svg)",""]
lines += ["[Original comparison CSV](comparison.csv) | [Attempt-level results CSV](attempt-results.csv)","",
          "The CSV includes saved attempt timings, convergence, iteration counts, residual location, voltage target error where available, and worker status. "
          "The case reports link to the detailed checks and logs.","",
          "## Interrupted case: " + (partial or "none"),"",
          "The run stopped part-way through this case. These saved attempts do not enter the win counts.","",
          "| Solver | Recorded | Converged | Mean solve ms | Worst residual (pu) |",
          "| --- | ---: | ---: | ---: | ---: |"]
for mode in modes if partial else ():
    n,g,t,r,_=summary[partial,mode]
    lines.append(f"| {labels[mode]} | {n}/10 | {g if n else '-'} | {format(t,'.8g') if t is not None else '-'} | {format(r,'.10g') if r is not None else '-'} |")
lines += ["","## Case evidence and run coverage","","| Case | Buses | Evidence |","| --- | ---: | --- |"]
for n,c in cases:
    stem=Path(c).stem
    folder=root/stem
    if (folder/"report.md").exists():
        link=f"[Report]({stem}/report.md) / [Manifest]({stem}/manifest.json)"
    elif c==partial:
        link=f"[Interrupted run log]({stem}.log)"
    elif (root/f"{stem}.log").exists():
        link=f"[No supported input: DC lines]({stem}.log)"
    else:
        link="Not reached"
    lines.append(f"| {c} | {n} | {link} |")
report=root/"progress.md"
original=(report.read_text(encoding="utf-8").split("\n## Charts and saved evidence",1)[0].rstrip()
          if report.exists() else "# AC power-flow suite")
report.write_text(original+"\n"+"\n".join(lines)+"\n",encoding="utf-8")
print(f"{len(attempts)} attempt records, {len(cases)} case coverage rows, {len(list(out.glob('*')))} charts")
if partial:
    print({mode:summary[partial,mode] for mode in modes})
