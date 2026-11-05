#!/usr/bin/env python3
"""RBF -> source conversion used by the GUI (gui.py) and usable from the command line.

Writes into the output folder, for an RBF named NAME.rbf:
  NAME.v, NAME.sidecar.json   rebuildable site-level source (netlist/sitev.py): editing a setting
                              and rebuilding gives a new RBF; unchanged source rebuilds byte for byte
  NAME_rtl.v                  synthesisable RTL Verilog: LUTs as boolean logic, FFs as always blocks,
                              RAMs as arrays with their contents, DSPs as multiplies (netlist/rtlv.py)
  NAME_sim.v, rbf_prims.v     behavioural simulation model of the bitstream (netlist/simv.py)
  NAME.ir.json                the decoded netlist IR (optional)
  NAME.rebuilt.rbf            the RBF rebuilt from NAME.v + sidecar (verification)
  NAME.summary.txt            what was produced and the verification result

Usage: python convert.py RBF [RBF ... | FOLDER] OUTDIR [--no-source] [--no-rtl] [--no-sim] [--no-verify] [--ir]
       (several RBFs, or a folder of them: each goes to OUTDIR/<name>/)
       python convert.py --rebuild NAME.v OUT.rbf      (rebuild an RBF from NAME.v + NAME.sidecar.json)
Only for open-source cores and your own designs.
"""
import hashlib, json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "netlist"))
import btir, rtlv, sitev, simv  # noqa: E402

# no console window flashing up for mistral-cv when run from the windowed GUI
NOWIN = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def convert(rbf, outdir, source=True, sim=True, verify=True, ir_json=False, rtl=True, log=print):
    """Run the selected steps; log(str) receives progress lines. Returns a result dict."""
    rbf, outdir = Path(rbf), Path(outdir)
    if not rbf.is_file():
        raise FileNotFoundError(f"RBF not found: {rbf}")
    if not btir.MCV.exists():
        raise FileNotFoundError(f"mistral-cv not built: {btir.MCV}")
    outdir.mkdir(parents=True, exist_ok=True)
    name = rbf.stem
    res = {"rbf": str(rbf), "sha256": sha256(rbf), "files": [], "verified": None}
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        bt = Path(td) / "a.bt"
        log(f"Decoding {rbf.name} ({rbf.stat().st_size:,} bytes) for {btir.MODEL} ...")
        r = subprocess.run([str(btir.MCV), "decomp", btir.MODEL, str(rbf), str(bt)], capture_output=True, text=True, **NOWIN)
        if r.returncode:
            raise RuntimeError(f"mistral-cv decomp failed (is this a {btir.MODEL} RBF?):\n{(r.stderr or r.stdout).strip()}")
        ir = btir.bt_to_ir(bt.read_text())
        log(f"  {len(ir['cells']):,} blocks, {len(ir['nets']):,} nets")
        if ir_json:
            p = outdir / f"{name}.ir.json"
            p.write_text(json.dumps(ir), encoding="utf-8")
            res["files"].append(p)
            log(f"Wrote {p.name}")
        if source or verify:
            stem = outdir / name if source else Path(td) / name
            log("Writing rebuildable source (site-level Verilog + sidecar) ...")
            sitev.emit(ir, stem)
            if source:
                res["files"] += [stem.with_suffix(".v"), Path(str(stem) + ".sidecar.json")]
                log(f"Wrote {name}.v and {name}.sidecar.json")
        if rtl:
            log("Writing RTL Verilog (synthesisable, no primitive library) ...")
            text, unmodelled = rtlv.emit(ir, "rbf_top")
            p = outdir / f"{name}_rtl.v"
            p.write_text(text, encoding="utf-8")
            res["files"].append(p)
            res["unmodelled"] = {k: v for k, v in unmodelled.items() if v}
            log(f"Wrote {p.name} ({p.stat().st_size / 1e6:.1f} MB)")
        if sim:
            log("Writing simulation model ...")
            text, unmodelled = simv.emit(ir, "rbf_top")
            p = outdir / f"{name}_sim.v"
            p.write_text(text, encoding="utf-8")
            shutil.copyfile(btir.RES / "sim" / "rbf_prims.v", outdir / "rbf_prims.v")
            res["files"] += [p, outdir / "rbf_prims.v"]
            res["unmodelled"] = {k: v for k, v in unmodelled.items() if v}
            log(f"Wrote {p.name} (+ rbf_prims.v)")
            if res["unmodelled"]:
                byblock = {}
                for k, v in res["unmodelled"].items():
                    b = k.split("(")[0]
                    byblock[b] = byblock.get(b, 0) + v
                log("  Not modelled yet (outputs left undriven): "
                    + ", ".join(f"{b} x{n}" for b, n in sorted(byblock.items())) + " (details in the summary)")
        if verify:
            log("Verifying: rebuilding the RBF from the source ...")
            ir2 = sitev.parse(stem.with_suffix(".v").read_text(), json.loads(Path(str(stem) + ".sidecar.json").read_text()))
            bt2 = Path(td) / "rebuilt.bt"
            bt2.write_text(btir.to_bt(ir2), newline="\n")
            out = outdir / f"{name}.rebuilt.rbf"
            r = subprocess.run([str(btir.MCV), "comp", str(bt2), str(out)], capture_output=True, text=True, **NOWIN)
            if r.returncode:
                raise RuntimeError(f"mistral-cv comp failed:\n{(r.stderr or r.stdout).strip()}")
            res["files"].append(out)
            res["verified"] = out.read_bytes() == rbf.read_bytes()
            log("  Rebuilt RBF is byte-identical to the original." if res["verified"]
                else "  WARNING: rebuilt RBF differs from the original.")
    res["seconds"] = round(time.time() - t0, 1)
    summary = outdir / f"{name}.summary.txt"
    lines = [f"Source RBF: {rbf}", f"SHA-256: {res['sha256']}", f"Device: {btir.MODEL}",
             f"Time: {res['seconds']} s",
             "Rebuild check: " + {None: "not run", True: "byte-identical", False: "DIFFERENT"}[res["verified"]],
             "Files:"] + [f"  {Path(f).name}" for f in res["files"]]
    if res.get("unmodelled"):
        lines.append(f"RTL / simulation model, not modelled yet (outputs undriven): {res['unmodelled']}")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    res["files"].append(summary)
    log(f"Done in {res['seconds']} s. Output: {outdir}")
    return res


def rebuild(source_v, out_rbf, log=print):
    """Rebuild an RBF from (possibly edited) site-level source NAME.v and its NAME.sidecar.json."""
    source_v, out_rbf = Path(source_v), Path(out_rbf)
    side = Path(str(source_v.with_suffix("")) + ".sidecar.json")
    if not source_v.is_file():
        raise FileNotFoundError(f"source not found: {source_v}")
    if not side.is_file():
        raise FileNotFoundError(f"sidecar not found next to the source: {side}")
    t0 = time.time()
    log(f"Reading {source_v.name} + {side.name} ...")
    ir = sitev.parse(source_v.read_text(), json.loads(side.read_text()))
    with tempfile.TemporaryDirectory() as td:
        bt = Path(td) / "rebuilt.bt"
        bt.write_text(btir.to_bt(ir), newline=chr(10))
        log("Compiling the bitstream ...")
        out_rbf.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([str(btir.MCV), "comp", str(bt), str(out_rbf)], capture_output=True, text=True, **NOWIN)
        if r.returncode:
            raise RuntimeError("mistral-cv comp failed: " + (r.stderr or r.stdout).strip())
    secs = round(time.time() - t0, 1)
    log(f"Done in {secs} s. Wrote {out_rbf} ({out_rbf.stat().st_size:,} bytes)")
    return {"rbf": str(out_rbf), "seconds": secs, "sha256": sha256(out_rbf)}


def collect(inputs):
    """RBF files from a list of files and/or folders (a folder contributes every *.rbf in it)."""
    out = []
    for i in inputs:
        p = Path(i)
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() == ".rbf" and f.is_file())
        elif p.suffix.lower() == ".rbf" or p.is_file():
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = str(p.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def convert_many(inputs, outdir, log=print, **opts):
    """Convert several RBFs. One RBF writes straight into outdir; several get outdir/<name>/ each.
    A failing RBF is logged and skipped. Returns [(rbf, result dict or None, error or None)]."""
    rbfs = collect(inputs)
    if not rbfs:
        raise FileNotFoundError("no .rbf files in the selection")
    outdir = Path(outdir)
    results = []
    for n, rbf in enumerate(rbfs, 1):
        dest = outdir if len(rbfs) == 1 else outdir / rbf.stem
        if len(rbfs) > 1:
            log(f"[{n}/{len(rbfs)}] {rbf.name} -> {dest}")
        try:
            results.append((rbf, convert(rbf, dest, log=log, **opts), None))
        except Exception as e:  # keep going with the rest of the batch
            log(f"  ERROR: {e}")
            results.append((rbf, None, str(e)))
            if len(rbfs) > 1 and dest.is_dir() and not any(dest.iterdir()):
                dest.rmdir()                    # no empty folder for a file that failed
    if len(rbfs) > 1:
        ok = sum(1 for _, r, e in results if r and r["verified"] in (None, True))
        log(f"Batch done: {ok}/{len(rbfs)} converted"
            + ("" if ok == len(rbfs) else f", {len(rbfs) - ok} failed or differ: "
               + ", ".join(r.name for r, res, e in results if not res or res["verified"] is False)))
    return results


def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    if "--rebuild" in flags and len(args) == 2:
        rebuild(args[0], args[1])
        return 0
    if len(args) < 2:
        print(__doc__)
        return 2
    results = convert_many(args[:-1], args[-1], source="--no-source" not in flags, sim="--no-sim" not in flags,
                           verify="--no-verify" not in flags, ir_json="--ir" in flags, rtl="--no-rtl" not in flags)
    return 0 if all(res and res["verified"] in (None, True) for _, res, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
