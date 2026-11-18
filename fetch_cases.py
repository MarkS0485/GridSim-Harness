"""Assemble the flat case folder for the extended suite from cases-extended.json.

Vendored cases are copied from cases/<source>/. Cases without a redistribution grant are downloaded from their pinned
commit on GitHub (raw.githubusercontent.com/<owner>/<repo>/<commit>/<path>). Every file is checked against its
SHA-256 in the catalogue; a mismatch stops the run. Files already present with the right hash are kept.

Usage: python fetch_cases.py --out DIR
"""
import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    catalogue = json.loads((HERE / "cases-extended.json").read_text(encoding="ascii"))["cases"]
    args.out.mkdir(parents=True, exist_ok=True)
    fetched = copied = kept = 0
    for name, entry in sorted(catalogue.items()):
        target = args.out / name
        if target.is_file() and sha256(target.read_bytes()) == entry["sha256"]:
            kept += 1
            continue
        if entry["distribution"] == "vendored":
            shutil.copyfile(HERE / "cases" / entry["source"] / name, target)
            copied += 1
        else:
            owner_repo = entry["repo"].removeprefix("https://github.com/")
            url = f"https://raw.githubusercontent.com/{owner_repo}/{entry['commit']}/{entry['path']}"
            with urllib.request.urlopen(url, timeout=120) as response:
                target.write_bytes(response.read())
            fetched += 1
        if sha256(target.read_bytes()) != entry["sha256"]:
            target.unlink()
            sys.exit(f"SHA-256 mismatch for {name}; refusing it")
    print(f"{len(catalogue)} cases in {args.out}: {copied} copied, {fetched} fetched, {kept} already present")


if __name__ == "__main__":
    main()
