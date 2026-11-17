"""Ten-chart pack from a run-suite.py output folder (needs attempt-results.csv, matplotlib, numpy).

Usage: python build_chart_pack.py SUITE_OUTPUT_DIR
"""
import csv
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter
from statistics import mean, median, quantiles

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if len(sys.argv) < 2:
    sys.exit(__doc__)
root = Path(sys.argv[1]).resolve()
suite = (json.loads((root / "suite-manifest.json").read_text(encoding="utf-8"))
         if (root / "suite-manifest.json").is_file() else {})
run_label = suite.get("runLabel", "Busy-machine rehearsal").upper()
run_date = suite.get("startedUtc", "")[:10]
out = root / "chart-pack"
out.mkdir(exist_ok=True)
with (root / "attempt-results.csv").open() as stream:
    records = list(csv.DictReader(stream))
modes = "dense sparse matpower pandapower pypsa veragrid powsybl andes powermodels".split()
labels = ["GridSim Dense","GridSim Sparse","MATPOWER","pandapower","PyPSA","VeraGrid","PowSyBl","ANDES","PowerModels"]
names = dict(zip(modes,labels))
colors = dict(zip(modes,["#9bafd0","#59efd1","#ffbe74","#fa92b3","#c9a1ff","#d9db77","#74baff","#ff8781","#89dc9d"]))
bg, panel, white, muted = "#111929","#18243a","#f5f8ff","#acbdd5"
plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"text.color":white,"axes.facecolor":panel,
                     "axes.labelcolor":white,"axes.edgecolor":panel,"xtick.color":muted,"ytick.color":muted,
                     "figure.facecolor":bg,"grid.color":"#35455e","svg.fonttype":"none"})
group = defaultdict(list)
for row in records:
    group[row["case"],row["path"]].append(row)
cases = sorted({(int(r["buses"]),r["case"]) for r in records})
# A case with saved attempts but no manifest is a run that was stopped part-way.
partial = next((case for _, case in cases
                if not (root / Path(case).stem / "manifest.json").exists()
                and any(row["case"] == case and row["converged"] == "True" for row in records)), None)
stats = {}
for key, rows in group.items():
    valid = [r for r in rows if r["converged"]=="True"]
    t = [float(r["elapsedMs"]) for r in valid if r["elapsedMs"]]
    v = [float(r["residualPu"]) for r in valid if r["residualPu"]]
    stats[key] = (t,v,len(valid))
def eligible(case, mode):
    t,v,n = stats.get((case,mode),([],[],0))
    return n==len(t)==len(v)==10 and max(v)<=1e-8
def ordered(case):
    return sorted((mean(stats[case,m][0]),m) for m in modes if eligible(case,m))
def setup(title,sub):
    fig,ax = plt.subplots(figsize=(12,6.75))
    fig.subplots_adjust(left=.13,right=.95,top=.76,bottom=.20)
    fig.text(.075,.94,"GRIDSIM  /  AC POWER FLOW",color=colors["sparse"],fontsize=10,fontweight="bold")
    fig.text(.075,.875,title,fontsize=26,fontweight="bold")
    fig.text(.075,.825,sub,fontsize=10,color=muted)
    fig.text(.075,.065,f"{run_date}  /  {run_label}  /  TEN ATTEMPTS PER FINISHED PATH",
             color=muted,fontsize=9)
    ax.grid(axis="y",alpha=.7)
    ax.set_axisbelow(True)
    return fig,ax
saved_titles = []


def save(fig, name, title=None):
    saved_titles.append(title or name)
    for ext in ("png","svg"):
        fig.savefig(out/f"{name}.{ext}",dpi=160)
    plt.close(fig)
def candles(ax,x,values,color):
    low,high=min(values),max(values)
    q1,_,q3=quantiles(values,n=4,method="inclusive")
    ax.vlines(x,low,high,color=color,lw=2)
    ax.bar(x,q3-q1,bottom=q1,width=.57,color=color,alpha=.72)
    ax.hlines(median(values),x-.28,x+.28,color=white,lw=2)

peg="case1354pegase.m"
have_peg = all((peg, m) in stats for m in modes)
if have_peg:
    fig,ax=setup("Nine solvers. One 1,354-bus grid.","PEGASE solve time per path. Wick = min-max, body = middle 50%, white tick = median.")
    for i,m in enumerate(modes):
        candles(ax,i,stats[peg,m][0],colors[m])
    ax.set(yscale="log",ylabel="Solve time (ms)",xticks=range(9),xticklabels=labels)
    ax.tick_params(axis="x",labelrotation=24)
    ax.text(.98,.95,"ANDES exceeds the common residual threshold",transform=ax.transAxes,color=muted,ha="right",va="top",fontsize=9)
    save(fig,"01-pegase-candlesticks")

if have_peg:
    fig,ax=setup("GridSim solve time as networks grow","Paired GridSim cases with ten converged attempts. The final pair is from the interrupted run.")
    pairs=[(b,c) for b,c in cases if all(len(stats.get((c,m),([],))[0])==10 for m in modes[:2])]
    for i,(b,c) in enumerate(pairs):
        for shift,m in [(-.19,"dense"),(.19,"sparse")]:
            vals=stats[c,m][0]
            q1,_,q3=quantiles(vals,n=4,method="inclusive")
            ax.vlines(i+shift,min(vals),max(vals),color=colors[m],lw=1.3)
            ax.bar(i+shift,q3-q1,bottom=q1,width=.32,color=colors[m])
            ax.hlines(median(vals),i+shift-.16,i+shift+.16,color=white,lw=.8)
    at=list(range(0,len(pairs),5))+[len(pairs)-1]
    ax.set(yscale="log",ylabel="Solve time (ms)",xlabel="Cases ordered by bus count",xticks=at,xticklabels=[pairs[i][0] for i in at])
    for m in modes[:2]: ax.bar([],[],color=colors[m],label=names[m])
    ax.legend(facecolor=panel,labelcolor=white,loc="upper left")
    save(fig,"02-gridsim-scaling-candlesticks")

    fig,ax=setup("Where Sparse gains on Dense","Ratio of mean solve times. Both paths passed the residual check. The diamond marks the interrupted case.")
    for b,c in pairs:
        if all(eligible(c,m) for m in modes[:2]):
            ratio=mean(stats[c,"dense"][0])/mean(stats[c,"sparse"][0])
            ax.scatter(b,ratio,color=colors["sparse"],s=55,marker="D" if c==partial else "o")
    ax.axhline(1,color=muted,ls="--")
    ax.set(xscale="log",yscale="log",xlabel="Bus count",ylabel="Dense time / Sparse time")
    save(fig,"03-sparse-speedup")

    fig,ax=setup("The nearest external challenger","Fastest qualifying external mean divided by GridSim Sparse mean. Higher favours Sparse.")
    for b,c in cases:
        ranked=ordered(c)
        external=next(((t,m) for t,m in ranked if m not in modes[:2]),None)
        if external and eligible(c,"sparse"):
            t,m=external
            ax.scatter(b,t/mean(stats[c,"sparse"][0]),color=colors[m],s=60,marker="D" if c==partial else "o")
    ax.axhline(1,color=muted,ls="--")
    ax.set(xscale="log",yscale="log",xlabel="Bus count (diamond = interrupted case)",ylabel="External time / Sparse time")
    for m in modes[2:]: ax.scatter([],[],color=colors[m],label=names[m])
    ax.legend(ncol=4,fontsize=8,facecolor=panel,labelcolor=white,loc="upper left")
    save(fig,"04-nearest-challenger")

    fig,ax=setup("Converged is only the first check","Completed case rows. The bright segment passed the independent residual check on all ten attempts.")
    for i,m in enumerate(modes):
        claimed=sum(stats.get((c,m),([],[],0))[2]==10 for _,c in cases if c!=partial)
        checked=sum(eligible(c,m) for _,c in cases if c!=partial)
        ax.barh(i,claimed,color="#384963")
        ax.barh(i,checked,color=colors[m])
        ax.text(claimed+.5,i,f"{checked} / {claimed}",va="center",fontsize=9)
    ax.set(yticks=range(9),yticklabels=labels,xlim=(0,63),xlabel="Cases: residual pass / solver-reported 10 of 10")
    ax.invert_yaxis()
    save(fig,"05-convergence-versus-check")

    fig,ax=setup("How far off was the power balance?","Worst independent residual per case and path. Each dot represents one case with a saved residual.")
    for i,m in enumerate(modes):
        vals=[max(v) for (c,mode),(t,v,n) in stats.items() if mode==m and v]
        jitter=np.linspace(-.26,.26,len(vals))
        ax.scatter([max(v,1e-17) for v in sorted(vals)],i+jitter,color=colors[m],s=22,alpha=.8)
    ax.axvline(1e-8,color=white,ls="--",lw=1)
    ax.set(xscale="log",yticks=range(9),yticklabels=labels,xlabel="Worst residual (pu). Dashed line = 1e-8 pu.")
    ax.invert_yaxis()
    save(fig,"06-residual-distributions")

    fig,ax=setup("Speed and power balance on PEGASE","Each point is a solver path on the 1,354-bus case. Faster and lower residual sit towards the lower left.")
    for i,m in enumerate(modes):
        t,v,_=stats[peg,m]
        ax.scatter(mean(t),max(v),color=colors[m],s=88,label=names[m])
    ax.axhline(1e-8,color=white,ls="--",lw=1)
    ax.set(xscale="log",yscale="log",xlabel="Mean solve time (ms)",ylabel="Worst residual (pu)")
    ax.legend(ncol=3,fontsize=8,facecolor=panel,labelcolor=white,loc="upper right")
    save(fig,"07-pegase-speed-versus-residual")

if partial:
    fig,ax=setup("Results that survived the stop",f"{partial}. Paths with ten saved converged attempts and a passing residual check.")
    four=[m for m in modes if eligible(partial,m)]
    for i,m in enumerate(four):
        t,v,_=stats[partial,m]
        candles(ax,i,t,colors[m])
        ax.annotate(f"{mean(t):,.1f} ms mean",(i,max(t)),xytext=(0,9),textcoords="offset points",ha="center",color=white,fontsize=9)
    ax.set(yscale="log",ylabel="Solve time (ms)",xticks=range(len(four)),xticklabels=[names[m] for m in four])
    ax.margins(y=.18)
    save(fig,"08-interrupted-2000-bus-candles")

wins=Counter()
for _,case in cases:
    ranking = ordered(case)
    if ranking and case!=partial:
        wins[ranking[0][1]]+=1
if ("case1197.m","dense") in stats and ("case1197.m","sparse") in stats:
    fig,ax=setup("Compute-time wins among qualifying cases","Fastest mean solve time among paths with ten converged attempts and worst residual at or below 1e-8 pu.")
    ax.set_axis_off()
    total=sum(wins.values())
    ax.text(.05,.52,f"{wins['sparse']} / {total}",transform=ax.transAxes,color=colors["sparse"],fontsize=98,fontweight="bold")
    ax.text(.055,.29,"QUALIFYING CASES WON ON COMPUTE TIME",transform=ax.transAxes,fontsize=16,fontweight="bold")
    ax.text(.055,.13,f"{len({c for _,c in cases if c!=partial})} case rows reported, any interrupted case is outside the win count.",
            transform=ax.transAxes,fontsize=10,color=muted)
    save(fig,"09-headline-compute-wins")

    fig,ax=setup("Sparse versus Dense, same result bits","GridSim Sparse versus GridSim Dense on the 1,197-bus case.")
    case="case1197.m"
    dense=mean(stats[case,"dense"][0])
    sparse=mean(stats[case,"sparse"][0])
    ax.set_axis_off()
    ax.text(.06,.53,f"{dense/sparse:.0f}x",transform=ax.transAxes,color=colors["sparse"],fontsize=96,fontweight="bold")
    ax.text(.063,.33,f"{dense:,.1f} ms Dense   /   {sparse:.2f} ms Sparse",transform=ax.transAxes,fontsize=20,fontweight="bold")
    ax.text(.064,.15,"Ten attempts on each path. Bit differences are reported in the case manifest.",
            transform=ax.transAxes,fontsize=11,color=muted)
    save(fig,"10-headline-1197-bus")

index = ["","## Ten-chart benchmark pack","",
         "Candlesticks show the minimum, first quartile, median, third quartile and maximum of the saved timings. "
         "The interrupted case is marked where shown. Run: " + run_label.lower() + ".",""]
for p in sorted(out.glob("*.png")):
    title=p.stem.split("-",1)[-1].replace("-"," ").capitalize()
    rel=p.relative_to(root).as_posix()
    index.append(f"- [{title}]({rel}) ([SVG]({p.with_suffix('.svg').relative_to(root).as_posix()}))")
report = root/"progress.md"
old=report.read_text(encoding="utf-8").split("\n## Ten-chart benchmark pack",1)[0].rstrip()
report.write_text(old+"\n"+"\n".join(index)+"\n",encoding="utf-8")
print("Charts:",len(list(out.glob("*.png"))), "Win counts:",wins)
