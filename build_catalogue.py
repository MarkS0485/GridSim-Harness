"""Build the extended case catalogue (cases-extended.json) and vendor the redistributable cases into cases/.

Sources are pinned git checkouts (passed with --src): MATPOWER (github.com/MATPOWER/matpower), PGLib-OPF
(github.com/power-grid-lib/pglib-opf) and GridSim-Models (github.com/MarkS0485/GridSim-Models). For every case
file the catalogue records its source repository, commit, path, SHA-256, bus count, licence and how the harness
obtains it:

* "vendored": the licence permits redistribution, so the file is copied into cases/<source>/ in this repository;
* "fetch": the file carries no redistribution grant, so fetch_cases.py downloads it at run time from the pinned
  commit on GitHub and checks its SHA-256.

Licence rules, by source:
* PGLib-OPF: data under CC BY 4.0 (its LICENSE), so vendored with the LICENSE kept in cases/LICENCES/.
* MATPOWER: the case files are NOT covered by MATPOWER's BSD licence (its LICENSE says so). A case is vendored only
  when its own header grants redistribution: "Creative Commons Attribution 4.0" (CC BY 4.0), or the NREL notice of
  case_RTS_GMLC.m ("use, copy, and distribute ... provided that this entire notice appears in all copies", kept in
  the file). Every other MATPOWER case is fetched, including those "distributed with permission" to MATPOWER (a
  permission given to MATPOWER, not to third parties).
* GridSim-Models: Open Database Licence 1.0 (its LICENCE / LICENSING.md), vendored whole with each model's
  ATTRIBUTION.txt, BUILD_README.md and provenance.json.

Duplicates: identical files (same SHA-256) are listed once. Files whose power-flow data (baseMVA, bus, gen columns
1-10, branch columns 1-11) is identical to an earlier file are kept but marked "pfEquivalentOf", and the runners skip
them: every PGLib "__sad" variant differs from its base only in branch angle limits, which a power flow ignores.

Usage: python build_catalogue.py --src DIR [--write-cases]
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = {
    "matpower": ("https://github.com/MATPOWER/matpower", "matpower", ["data/*.m"]),
    "pglib-opf": ("https://github.com/power-grid-lib/pglib-opf", "pglib-opf", ["*.m", "api/*.m", "sad/*.m"]),
}
MODELS = ("https://github.com/MarkS0485/GridSim-Models", "GridSim-Models")
NREL_NOTICE = "the user is granted the right, without any fee or cost, to use, copy, and distribute these data"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commit_of(repo):
    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def token(text):
    try:
        return float(text)
    except ValueError:
        return text


def matrix(text, name):
    found = re.search(rf"mpc\.{name}\s*=\s*\[(.*?)\];", text, re.S)
    if not found:
        return None
    rows = []
    for line in found.group(1).splitlines():
        line = line.split("%")[0].strip().rstrip(";").strip()
        if line:
            rows.append([token(value) for value in line.replace("\t", " ").split()])
    return rows


def power_flow_key(text):
    base = re.search(r"mpc\.baseMVA\s*=\s*([\d.eE+-]+)", text).group(1)
    data = [float(base), matrix(text, "bus"), [row[:10] for row in matrix(text, "gen")],
            [row[:11] for row in matrix(text, "branch")]]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


def header(text):
    end = text.find("mpc.version")
    return text[:end if end > 0 else 4000]


def matpower_licence(text):
    head = re.sub(r"\s+", " ", header(text).replace("%", " ")).lower()
    if "creative commons attribution 4.0" in head:
        return "CC-BY-4.0", "vendored"
    if NREL_NOTICE in head:
        return "NREL data notice (copy and distribute with the notice)", "vendored"
    if "with permission" in head or "permission of" in head:
        return "distributed with permission to MATPOWER (no third-party grant)", "fetch"
    return "none stated (MATPOWER case files are outside its BSD licence)", "fetch"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, required=True, help="Folder holding the pinned source checkouts")
    parser.add_argument("--write-cases", action="store_true", help="Copy vendored files into cases/")
    args = parser.parse_args()
    catalogue, by_hash, by_pf = {}, {}, {}
    for source, (url, folder, patterns) in SOURCES.items():
        repo = args.src / folder
        commit = commit_of(repo)
        for pattern in patterns:
            for path in sorted(repo.glob(pattern)):
                text = path.read_text(encoding="utf-8", errors="replace")
                if not re.search(r"^\s*function\s+mpc\s*=", text, re.M) or "mpc.bus" not in text:
                    continue   # helpers, scenario and profile files are not cases
                digest = sha256(path)
                if digest in by_hash:
                    catalogue[by_hash[digest]].setdefault("alsoAt", []).append(f"{source}:{path.relative_to(repo)}")
                    continue
                name = path.name
                if name in catalogue:
                    raise SystemExit(f"Two different files share the name {name}")
                if source == "pglib-opf":
                    licence, distribution = "CC-BY-4.0", "vendored"
                else:
                    licence, distribution = matpower_licence(text)
                key = power_flow_key(text)
                entry = dict(buses=len(matrix(text, "bus")), sha256=digest, source=source, repo=url, commit=commit,
                             path=str(path.relative_to(repo)), licence=licence, distribution=distribution,
                             tier="large" if len(matrix(text, "bus")) > 5000 else "standard")
                if key in by_pf:
                    entry["pfEquivalentOf"] = by_pf[key]
                else:
                    by_pf[key] = name
                catalogue[name] = entry
                by_hash[digest] = name
                if args.write_cases and distribution == "vendored":
                    target = HERE / "cases" / source / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
    models_repo = args.src / MODELS[1]
    manifest = {}
    for model in sorted((models_repo / "models").iterdir()):
        if not (model / "system.json").is_file():
            continue
        files = sorted(p for p in model.rglob("*") if p.is_file())
        buses = json.loads((model / "buses.json").read_text(encoding="utf-8"))
        count = len(buses) if isinstance(buses, list) else len(buses.get("buses", buses))
        manifest[model.name] = dict(buses=count, source="GridSim-Models", repo=MODELS[0],
                                    commit=commit_of(models_repo), path=str(model.relative_to(models_repo)),
                                    licence="ODbL-1.0", distribution="vendored", tier="models",
                                    files={str(p.relative_to(model)): sha256(p) for p in files})
        if args.write_cases:
            shutil.copytree(model, HERE / "cases" / "models" / model.name, dirs_exist_ok=True)
    if args.write_cases:
        licences = HERE / "cases" / "LICENCES"
        licences.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.src / "pglib-opf" / "LICENSE", licences / "PGLIB-OPF-LICENSE.txt")
        shutil.copyfile(args.src / "matpower" / "LICENSE", licences / "MATPOWER-LICENSE.txt")
        shutil.copyfile(models_repo / "LICENCE", licences / "GRIDSIM-MODELS-ODbL-1.0.txt")
        shutil.copyfile(models_repo / "LICENSING.md", licences / "GRIDSIM-MODELS-LICENSING.md")
    out = dict(cases=catalogue, models=manifest)
    (HERE / "cases-extended.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="ascii")
    vendored = sum(e["distribution"] == "vendored" for e in catalogue.values())
    equivalent = sum("pfEquivalentOf" in e for e in catalogue.values())
    print(f"{len(catalogue)} unique case files ({vendored} vendored, {len(catalogue) - vendored} fetched at run time; "
          f"{equivalent} power-flow-equivalent to another), {len(manifest)} GridSim models")


if __name__ == "__main__":
    main()
