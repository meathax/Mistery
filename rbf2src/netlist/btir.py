#!/usr/bin/env python3
"""Lossless netlist IR built from a mistral-cv decompile (.bt).

The IR regroups everything in the .bt:
  options     non-default option settings          {name: value}
  cells       non-default block settings per site  {site: {"type": T, "settings": {mux[.idx]: value}}}
  nets        routing trees, one per driver        [{"driver": node, "sinks": [...], "links": [[src, dst], ...]}]
  inversions  programmable inverters               {node: 0/1}
  raw         bits no decoded setting accounts for [["xc"|"xp"|"xo", a, b, v], ...]

A node is either a routing node (no ':' in its name) or a block port
"BT.XXX.YYY[.BI]:PORT[.PI]". Every routing node has one selected source, so the
active links form a forest; each tree is a net rooted at its driver.

to_bt() writes the IR back as .bt text; feeding it to `mistral-cv comp` gives
back the original RBF byte for byte (checked by `btir.py check`).

Usage:
  python -I btir.py ir   in.bt out.json
  python -I btir.py bt   in.json out.bt
  python -I btir.py check RBF... [--mcv PATH]   rbf -> bt -> IR -> bt -> rbf must be identical
"""
import json, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

MODEL = "5CSEBA6U23I7"
ROOT = Path(__file__).resolve().parents[2]
# Frozen app (PyInstaller, rbf2src/packaging): mistral-cv.exe and its DLLs sit next to the app exe.
FROZEN = getattr(sys, "frozen", False)
MCV = (Path(sys.executable).parent / "mistral-cv" / "mistral-cv.exe" if FROZEN
       else ROOT / "build-win" / "mistral-cv" / "mistral-cv.exe")
# Bundled read-only resources (sim/rbf_prims.v, tables/p2p.txt) in the frozen app, else the source tree.
RES = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parents[1]


def is_port(node: str) -> bool:
    return ":" in node


def site_of(node: str) -> str:
    """Block site of a port node: 'LAB.088.010.9:D' -> 'LAB.088.010'."""
    return ".".join(node.split(":")[0].split(".")[:3])


def parse_bt(text: str) -> dict:
    ir = {"model": None, "options": {}, "cells": {}, "links": [], "inversions": {}, "raw": []}
    ir["pins"] = {}
    for line in text.splitlines():
        line, _, comment = line.partition(";")
        line = line.strip()
        if not line:
            continue
        f = line.split()
        # GPIO settings carry the package pin as a comment: keep site.bi -> pin
        if f[0] == "s" and f[1].startswith("GPIO.") and comment.strip():
            site, mux = f[1].split(":")
            bi = mux.split(".")[1] if "." in mux else None
            if bi is not None:
                ir["pins"].setdefault(f"{site}.{bi}", comment.strip().split()[0])
        k = f[0]
        if k == "m":
            ir["model"] = f[1]
        elif k == "o":
            ir["options"][f[1]] = f[2]
        elif k == "r":
            ir["links"].append([f[1], f[2]])
        elif k == "s":
            site, mux = f[1].split(":")
            c = ir["cells"].setdefault(site, {"type": site.split(".")[0], "settings": {}})
            c["settings"][mux] = f[2]
        elif k == "i":
            ir["inversions"][f[1]] = int(f[2])
        elif k in ("xc", "xp", "xo"):
            ir["raw"].append([k, int(f[1]), int(f[2]), int(f[3])])
        else:
            raise ValueError(f"unknown bt keyword {k!r}")
    return ir


def trace_nets(links: list) -> list:
    src_of, outs = {}, defaultdict(list)
    for s, d in links:
        if d in src_of:
            raise ValueError(f"node {d} has two sources: {src_of[d]} and {s}")
        src_of[d] = s
        outs[s].append(d)
    nets = []
    for root in sorted(n for n in outs if n not in src_of):
        net = {"driver": root, "sinks": [], "links": []}
        stack = [root]
        while stack:
            n = stack.pop()
            if n not in outs:
                net["sinks"].append(n)
                continue
            for d in outs[n]:
                net["links"].append([n, d])
                stack.append(d)
        net["sinks"].sort()
        nets.append(net)
    # A cycle without a root would be lost silently; refuse it.
    covered = sum(len(n["links"]) for n in nets)
    if covered != len(links):
        raise ValueError(f"{len(links) - covered} links are not reachable from any driver (routing loop?)")
    return nets


def bt_to_ir(text: str) -> dict:
    ir = parse_bt(text)
    ir["nets"] = trace_nets(ir.pop("links"))
    return ir


def to_bt(ir: dict) -> str:
    out = [f"m {ir['model']}"]
    out += [f"o {k} {v}" for k, v in ir["options"].items()]
    for net in ir["nets"]:
        out += [f"r {s} {d}" for s, d in net["links"]]
    for site, c in ir["cells"].items():
        out += [f"s {site}:{m} {v}" for m, v in c["settings"].items()]
    out += [f"i {n} {v}" for n, v in ir["inversions"].items()]
    out += [f"{k} {a} {b} {v}" for k, a, b, v in ir["raw"]]
    return "\n".join(out) + "\n"


def summary(ir: dict) -> dict:
    types = defaultdict(int)
    for c in ir["cells"].values():
        types[c["type"]] += 1
    port_sinks = sum(1 for n in ir["nets"] for s in n["sinks"] if is_port(s))
    dangling = sum(1 for n in ir["nets"] for s in n["sinks"] if not is_port(s))
    return {"cells": dict(sorted(types.items())), "nets": len(ir["nets"]),
            "nets_from_ports": sum(1 for n in ir["nets"] if is_port(n["driver"])),
            "port_sinks": port_sinks, "routing_leaf_sinks": dangling, "raw_bits": len(ir["raw"])}


def check(rbfs, mcv: Path) -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for rbf in map(Path, rbfs):
            bt, bt2, out = td / "a.bt", td / "b.bt", td / "b.rbf"
            subprocess.run([str(mcv), "decomp", MODEL, str(rbf), str(bt)], check=True, capture_output=True)
            ir = bt_to_ir(bt.read_text())
            bt2.write_text(to_bt(json.loads(json.dumps(ir))), newline="\n")
            subprocess.run([str(mcv), "comp", str(bt2), str(out)], check=True, capture_output=True)
            same = out.read_bytes() == rbf.read_bytes()
            bad += not same
            print(f"{rbf.name}: {'IDENTICAL' if same else 'DIFFERENT'} {json.dumps(summary(ir))}")
    return 1 if bad else 0


def main(argv):
    if argv[0] == "ir":
        ir = bt_to_ir(Path(argv[1]).read_text())
        Path(argv[2]).write_text(json.dumps(ir, indent=0), newline="\n")
        print(json.dumps(summary(ir)))
    elif argv[0] == "bt":
        Path(argv[2]).write_text(to_bt(json.loads(Path(argv[1]).read_text())), newline="\n")
    elif argv[0] == "check":
        mcv = Path(argv[argv.index("--mcv") + 1]) if "--mcv" in argv else MCV
        return check([a for a in argv[1:] if a != "--mcv" and Path(a) != mcv], mcv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
