#!/usr/bin/env python3
"""Site-level Verilog netlist <-> IR.

emit(ir) writes two files:
  <name>.v            one module `top`: one instance per configured site
                      (module name mistral_<BLOCKTYPE>, every non-default
                      setting as a string parameter) and one wire per net,
                      named after its driver port; GPIO pads used by the design
                      are top-level ports named after their package pin.
  <name>.sidecar.json everything physical that is not logic: model, options,
                      the routing of each net (by wire name), inversions,
                      raw bits.

parse(v_text, sidecar) gives back the IR, so that
  RBF -> decomp -> IR -> emit -> parse -> IR -> bt -> comp
reproduces the RBF byte for byte while the netlist is unchanged. Editing a
parameter (a LUT mask, a FF option) is carried into the rebuild as a setting
change. Connectivity changes need re-routing, which parse() refuses for now
(it checks every wire's driver and sinks against the routing in the sidecar).

Port naming (bijective with Mistral port nodes):
  SITE[.BI]:PT[.PI]  ->  instance SITE with '.' -> '_', port PT[_pPI][_bBI]
"""
import json, re
from pathlib import Path

ID = re.compile(r"[^A-Za-z0-9_]")


def inst_name(site: str) -> str:
    return site.replace(".", "_")


def port_split(node: str):
    """'LAB.088.010.3:A' -> ('LAB.088.010', 'A_b3'); 'GPIO.089.009.0:DATAOUT.0' -> ('GPIO.089.009', 'DATAOUT_p0_b0')."""
    blk, p = node.split(":")
    f = blk.split(".")
    site = ".".join(f[:3])
    bi = f[3] if len(f) > 3 else None
    pt, _, pi = p.partition(".")
    name = pt + (f"_p{pi}" if pi else "") + (f"_b{bi}" if bi is not None else "")
    return site, name


def port_join(site: str, port: str) -> str:
    m = re.fullmatch(r"(.+?)(?:_p(\d+))?(?:_b(\d+))?", port)
    pt, pi, bi = m.group(1), m.group(2), m.group(3)
    return site + (f".{bi}" if bi is not None else "") + ":" + pt + (f".{pi}" if pi is not None else "")


def wire_name(driver: str) -> str:
    return "n_" + ID.sub("_", driver)


def param_name(mux: str) -> str:
    return mux.replace(".", "__")


def emit(ir: dict, stem: Path) -> None:
    nets = ir["nets"]
    conns = {}          # site -> {port: wire}
    sidecar_routes = {}
    for net in nets:
        w = wire_name(net["driver"])
        sidecar_routes[w] = net["links"]
        for node in [net["driver"]] + net["sinks"]:
            if ":" in node:
                site, port = port_split(node)
                conns.setdefault(site, {})[port] = w
    sites = sorted(set(ir["cells"]) | set(conns))
    pins = ir.get("pins", {})
    pad_ports = sorted(set(pins.values()))
    v = ["// Site-level netlist recovered by rbf2src from a Cyclone V bitstream.",
         f"// Model {ir['model']}. Physical routing lives in {stem.name}.sidecar.json.",
         "module top(" + ", ".join(f"inout wire {ID.sub('_', p)}" for p in pad_ports) + ");"]
    for net in nets:
        w = wire_name(net["driver"])
        v.append(f"  wire {w};")
    for site in sites:
        cell = ir["cells"].get(site, {"type": site.split(".")[0], "settings": {}})
        params = cell["settings"]
        lines = [f"  mistral_{cell['type']}"]
        if params:
            lines[0] += " #("
            lines += [f"    .{param_name(k)}(\"{val}\")," for k, val in params.items()]
            lines[-1] = lines[-1].rstrip(",")
            lines.append("  )")
        ports = [f"    .{p}({w})" for p, w in sorted(conns.get(site, {}).items())]
        ports += [f"    .PAD_b{key.split('.')[-1]}({ID.sub('_', pin)})" for key, pin in sorted(pins.items())
                  if ".".join(key.split(".")[:3]) == site]
        head = f"{inst_name(site)} (" + ("" if ports else ");")
        if params:
            lines.append(f"  {head}")
        else:
            lines[0] += f" {head}"
        if ports:
            lines += [p + "," for p in ports[:-1]] + [ports[-1], "  );"]
        v += lines
    v.append("endmodule")
    stem.with_suffix(".v").write_text("\n".join(v) + "\n", newline="\n")
    side = {"model": ir["model"], "options": ir["options"], "routes": sidecar_routes,
            "inversions": ir["inversions"], "raw": ir["raw"], "pins": pins,
            "site_order": sites}
    Path(str(stem) + ".sidecar.json").write_text(json.dumps(side, indent=0), newline="\n")


def _site(inst: str) -> str:
    bt, x, y = inst.rsplit("_", 2)
    return f"{bt}.{x}.{y}"


def parse(v_text: str, side: dict) -> dict:
    """Read back a netlist written by emit() (one statement per line, as emitted)."""
    cells, conns = {}, {}
    state, cur = None, None
    for line in v_text.splitlines():
        s = line.strip()
        if state is None:
            if s.startswith("mistral_"):
                cur = {"type": s.split()[0][len("mistral_"):], "settings": {}}
                if s.endswith("#("):
                    state = "params"
                else:
                    cur_site = _site(s.split()[1])
                    cells[cur_site] = cur
                    state = None if s.endswith(");") else "ports"
            continue
        if state == "params":
            if s == ")":
                state = "name"
                continue
            m = re.fullmatch(r'\.(\w+)\("([^"]*)"\),?', s)
            cur["settings"][m.group(1)] = m.group(2)
            continue
        if state == "name":
            cur_site = _site(s.split()[0])
            cells[cur_site] = cur
            state = None if s.endswith(");") else "ports"
            continue
        if state == "ports":
            if s == ");":
                state = None
                continue
            m = re.fullmatch(r"\.(\w+)\((\w+)\),?", s)
            if not m.group(1).startswith("PAD_"):
                conns.setdefault(m.group(2), []).append(port_join(cur_site, m.group(1)))
    # parameters back to mux names: '__' separates the mux name from its index
    for c in cells.values():
        c["settings"] = {k.replace("__", "."): v for k, v in c["settings"].items()}
    nets = []
    for w, links in side["routes"].items():
        srcs, dsts = {s for s, _ in links}, {d for _, d in links}
        ends = {n for n in (srcs - dsts) | (dsts - srcs) if ":" in n}
        if set(conns.get(w, [])) != ends:
            raise ValueError(f"wire {w} connectivity differs from its routing; re-routing is not implemented")
        srcs = dsts
        nets.append({"driver": next(s for s, _ in links if s not in srcs),
                     "sinks": sorted({d for _, d in links} - {s for s, _ in links}), "links": links})
    order = {s: i for i, s in enumerate(side["site_order"])}
    cells = {k: cells[k] for k in sorted(cells, key=lambda s: order.get(s, 1 << 30)) if cells[k]["settings"]}
    return {"model": side["model"], "options": side["options"], "cells": cells, "nets": nets,
            "inversions": side["inversions"], "raw": side["raw"], "pins": side.get("pins", {})}
