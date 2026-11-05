#!/usr/bin/env python3
"""Emit a simulatable behavioural Verilog netlist from an RBF (via the btir IR).

Every configured LAB/MLAB ALM becomes an rbf_alm instance and every LAB an rbf_labctl
(sim/rbf_prims.v), with parameters decoded from the Mistral settings (FINDINGS M011-M016).
Routing trees become wires. Package pins become ports named pad_<PIN> (input when the
GPIO drives DATAIN.0 into the fabric, output when a net reaches GPIO DATAOUT.0). Routing
trees whose root is not a block port (global/regional clock networks, whose source Mistral
does not model) become inputs named root_<node>. Blocks without a model (M10K, DSP, PLL,
HPS, MLAB in RAM mode) are reported and their outputs left undriven (x).

Usage: python -I simv.py RBF OUT.v [--top NAME]
"""
import re, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import alm as almm, btir  # noqa: E402

PINS = ["A", "B", "C", "D", "E0", "F0", "E1", "F1"]
OUTS = ["FFT0", "FFT1", "FFT1L", "FFB0", "FFB1", "FFB1L"]
TM = {"E_0": 0, "F_0": 1, "D_E": 2, "D_F": 3}
BM = {"E_1": 0, "F_1": 1, "C_E": 2, "C_F": 3}
LABPORTS = {"CLKIN.0": "clkin0", "CLKIN.1": "clkin1", "DATAIN.0": "din0", "DATAIN.1": "din1",
            "DATAIN.2": "din2", "DATAIN.3": "din3", "ACLR.0": "gaclr0", "ACLR.1": "gaclr1"}


_P2P = None


def p2p():
    """Fixed block-port connections from `mistral-cv p2p` (cached in build/tables/p2p.txt)."""
    global _P2P
    if _P2P is None:
        cache = (btir.RES / "tables" / "p2p.txt" if btir.FROZEN
                 else Path(__file__).resolve().parents[1] / "build" / "tables" / "p2p.txt")
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(subprocess.run([str(btir.MCV), "p2p", btir.MODEL], capture_output=True, text=True, check=True).stdout)
        _P2P = defaultdict(list)
        for line in cache.read_text().splitlines():
            f = line.split()
            if len(f) == 2:
                _P2P[f[0]].append(f[1])
                _P2P[f[1]].append(f[0])
    return _P2P


def hmc_pad(port, pins):
    """HMC IOINT bypass port -> package pin. Measured (FINDINGS M016): IOINT<x>DOUT.n is
    PHYDDIO<x>DOUT.n, which p2p wires to a GPIO DATAOUT.0; IOINT<x>DIN.n is DDIOPHY<x>DIN.n,
    fed by a GPIO DATAIN."""
    blk, pn = port.split(":")
    phy = pn.replace("IOINT", "PHYDDIO") if "DOUT" in pn else pn.replace("IOINT", "DDIOPHY")
    for g in p2p().get(f"{blk}:{phy}", []):
        if g.startswith("GPIO."):
            return pins.get(".".join(g.split(":")[0].split(".")[:4]))
    return None


# MLAB RAM contents (t0_mlab_init, FINDINGS M018): word w of the top half is raw LUT_MASK bit
# MLAB_WORD_BIT[w], bottom half bit 32 + MLAB_WORD_BIT[w], stored inverted
MLAB_WORD_BIT = [18, 19, 22, 23, 26, 27, 30, 31, 15, 14, 11, 10, 7, 6, 3, 2,
                 16, 17, 20, 21, 24, 25, 28, 29, 13, 12, 9, 8, 5, 4, 1, 0]


def mlab_init(raw):
    """Raw MLAB LUT_MASK -> rbf_alm RAMINIT: [31:0] top words, [63:32] bottom words."""
    top = sum((1 - (raw >> MLAB_WORD_BIT[w] & 1)) << w for w in range(32))
    bot = sum((1 - (raw >> (32 + MLAB_WORD_BIT[w]) & 1)) << w for w in range(32))
    return top | bot << 32


MLAB_WADDR_ALM = [2, 3, 7, 6, 1]   # MLAB RAM write address bit k -> ALM whose F1 carries it

# M10K settings seen in co-simulated designs (t0_m10k_init, t0_m10k_narrow); others stay unmodelled
M10K_KNOWN = {"A_DATA_FLOW_THRU", "A_DATA_WIDTH", "A_FAST_WRITE", "A_SA_WREN_DELAY", "A_SAEN_DELAY", "A_WL_DELAY",
              "A_WR_TIMER_PULSE", "B_DATA_FLOW_THRU", "B_DATA_WIDTH", "B_FAST_WRITE", "B_SA_WREN_DELAY",
              "B_SAEN_DELAY", "B_WL_DELAY", "B_WR_TIMER_PULSE", "DISABLE_UNUSED", "TOP_CLK_SEL", "TOP_W_SEL",
              "BOT_W_SEL", "TRUE_DUAL_PORT",
              # t0_m10k_ctrl (M021): port B on clock 1, output register (+clock 1, +aclr), read enable,
              # inverted port A write enable, port A clock enable
              "BOT_1_CORECLK_SEL", "BOT_1_INCLK_SEL", "BOT_1_OUTCLK_SEL", "BOT_CLK_SEL", "B_OUTPUT_SEL",
              "B_OUTCLR_EN", "BOT_R_SEL", "TOP_R_SEL", "TOP_W_INV", "TOP_CE0_SEL", "TOP_CORECLK_SEL",
              # t0_m10k_clkinv: inverted port clocks
              "TOP_CLK_INV", "BOT_CLK_INV",
              # t0_m10k_ena (M022): port B WE inversion, clock enables, 40-bit output register enable/clear
              "BOT_W_INV", "BOT_CE0_SEL", "BOT_CORECLK_SEL", "BOT_INCLK_SEL", "TOP_OUTCLK_SEL",
              "A_OUTPUT_SEL", "A_OUTCLR_EN"}

M10K_PHYS = [4 * (i % 10) + (0, 2, 1, 3)[i // 10] for i in range(40)]   # logical bit -> RAM.* bit


def m10k_init(s):
    """256 logical 40-bit words; logical bit i of a word is RAM.a bit M10K_PHYS[i], inverted."""
    words = []
    for a in range(256):
        r = int(s.get(f"RAM.{a}", "0").replace(".", ""), 16)
        words.append(sum((1 - ((r >> M10K_PHYS[i]) & 1)) << i for i in range(40)))
    return words


def ident(s):
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def alm_params(s, a, block):
    g = lambda k, d: s.get(f"{k}.{a}", d)
    m = almm.lab_mask(int("".join(s[f"LUT_MASK.{a}"].split(".")), 16), block)
    ram = g("TMODE", "") == "RAM"
    p = {"MASK": f"64'h{m:016x}", "TMODE": 0 if ram else TM[g("TMODE", "D_E")],
         "BMODE": 0 if ram else BM[g("BMODE", "C_E")], "RAM": int(ram),
         "RAMINIT": f"64'h{mlab_init(int(''.join(s[f'LUT_MASK.{a}'].split('.')), 16)) if ram else 0:016x}",
         "ARITH": int(g("ARITH_SEL", "LUT") == "ADDER"), "SHARE": int(g("SHARE", "0") == "1"),
         "TFB": int(g("T_FEEDBACK_SEL", "0")), "BFB": int(g("B_FEEDBACK_SEL", "0"))}
    for o in OUTS:
        p[o.replace("FF", "")[0] + "DFF" + o[3:]] = int(g(o.replace("FF", "")[0] + "DFF" + o[3:], "REG") == "NLUT")
    pk = {1: "TPKREG1", 2: "TPKREG0", 4: "BPKREG1", 5: "BPKREG0"}
    clkg = {1: "T", 5: "T", 2: "B", 4: "B"}
    half = {1: "T", 2: "T", 4: "B", 5: "B"}
    for sl in (1, 2, 4, 5):
        p[f"PK{sl}"] = int(g(pk[sl], "0") == "1")
        c = g(f"{clkg[sl]}CLK_SEL", "OFF")
        p[f"CK{sl}"] = 3 if c == "OFF" else int(c[-1])
        sl_en = g(f"{half[sl]}SLOAD_EN", "0") == "1"
        p[f"SC{sl}"] = int(g(f"{half[sl]}SCLR_DIS", "0") != "1" and (sl_en or p[f"PK{sl}"]))
    p["TEF"] = int(g("TEF_SEL", "E") == "F")
    p["BEF"] = int(g("BEF_SEL", "E") == "F")
    p["TCLR"] = int(g("TCLR_SEL", "0"))
    p["BCLR"] = int(g("BCLR_SEL", "0"))
    p["TSL"] = int(g("TSLOAD_EN", "0") == "1")
    p["BSL"] = int(g("BSLOAD_EN", "0") == "1")
    return p


def labctl_params(s, conn):
    g = s.get
    bits = lambda f: "3'b" + "".join(str(int(f(k))) for k in (2, 1, 0))
    return {"CLKA_DIN": int(g("CLKA_SEL", "CIN0") == "DIN0"), "CLKB_DIN": int(g("CLKB_SEL", "CIN1") == "DIN1"),
            "CLK_B": bits(lambda k: g(f"CLK{k}_INV", "0") == "1"),
            "CLK_INV": bits(lambda k: g(f"CLK{k}_SEL", "CLKA") == "CLKB"),
            "EN_USE": bits(lambda k: g(f"EN{k}_EN", "1") == "1"),
            "EN_INV": bits(lambda k: g(f"EN{k}_NINV", "1") == "1"),
            "ACLR0_GLB": int(g("ACLR0_SEL", "DIN2") == "ACLR0"), "ACLR1_GLB": int(g("ACLR1_SEL", "DIN3") == "ACLR1"),
            "ACLR0_INV": int(g("ACLR0_INV", "0") == "1"), "ACLR1_INV": int(g("ACLR1_INV", "0") == "1"),
            "CONN": "6'b" + "".join(str(int(p in conn)) for p in ("ACLR.1", "ACLR.0", "DATAIN.3", "DATAIN.2", "DATAIN.1", "DATAIN.0")),
            "SCLR_ON": int(g("SCLR_DIS", "0") != "1"), "SCLR_DIN1": int(g("SCLR_SEL", "DIN3") == "DIN1"),
            "SCLR_INV": int(g("SCLR_INV", "0") == "1"),
            "SLOAD_ON": int(g("SLOAD_EN", "1") == "1"), "SLOAD_DIN2": int(g("SLOAD_SEL", "DIN1") == "DIN2"),
            "SLOAD_INV": int(g("SLOAD_INV", "0") == "1")}


def emit(ir, top="rbf_top"):
    cells = ir["cells"]
    wire_of = {}            # sink/driver port -> wire name
    wires, assigns = [], []
    inputs, outputs = [], []
    unmodelled = defaultdict(int)
    for i, net in enumerate(ir["nets"]):
        d = net["driver"]
        w = f"n{i}"
        wires.append(w)
        if ":" not in d:
            r = "root_" + ident(d)
            inputs.append(r)
            assigns.append(f"assign {w} = {r};")
        elif d.startswith("GPIO.") and d.split(":")[1].startswith("DATAIN"):
            pin = ir["pins"].get(".".join(d.split(":")[0].split(".")[:4]), ident(d))
            inputs.append(f"pad_{pin}")
            assigns.append(f"assign {w} = {'~' if ir['inversions'].get(d, 0) else ''}pad_{pin};")
        elif d.startswith("HMC.") and ":IOINT" in d and hmc_pad(d, ir["pins"]):
            pin = hmc_pad(d, ir["pins"])
            inputs.append(f"pad_{pin}")
            assigns.append(f"assign {w} = pad_{pin};")
        else:
            wire_of[d] = w
            if not d.startswith(("LAB.", "MLAB.", "M10K.", "DSP.")):
                unmodelled[d.split(".")[0]] += 1
        for s in net["sinks"]:
            wire_of[s] = w
            pin = None
            if s.startswith("GPIO.") and s.split(":")[1].startswith("DATAOUT.0"):
                pin = ir["pins"].get(".".join(s.split(":")[0].split(".")[:4]), ident(s))
            elif s.startswith("HMC.") and ":IOINT" in s and "DOUT" in s:
                pin = hmc_pad(s, ir["pins"])
            if pin:
                inv = "~" if ir["inversions"].get(s, 0) else ""   # programmable port inverter
                outputs.append(f"pad_{pin}")
                assigns.append(f"assign pad_{pin} = {inv}{w};")
    body = []
    labs = defaultdict(set)
    for site, c in cells.items():          # carry/share chain wires first: chains cross LABs
        if c["type"] in ("LAB", "MLAB"):
            body.append("wire " + ", ".join(f"{ident(site)}_c{a}, {ident(site)}_s{a}" for a in range(10)) + ";")
    for site, c in cells.items():
        if c["type"] not in ("LAB", "MLAB"):
            continue
        s = c["settings"]
        if c["type"] == "MLAB" and any(k.startswith("RAM_DIS") for k in s) is False and False:
            pass
        # all ten ALMs: an ALM with only default settings still passes the carry/share chain
        alms = list(range(10))
        conn = {p for p in LABPORTS if f"{site}:{p}" in wire_of}
        lid = ident(site)
        lp = labctl_params(s, conn)
        ports = ", ".join(f".{v}({wire_of.get(f'{site}:{p}', chr(39) + 'b0')})".replace("'b0", "1'b0") for p, v in LABPORTS.items())
        body.append(f"wire [2:0] {lid}_clk, {lid}_ena; wire [1:0] {lid}_aclrn; wire {lid}_sclr, {lid}_sload;")
        body.append(f"rbf_labctl #({', '.join(f'.{k}({v})' for k, v in lp.items())}) {lid}_ctl ({ports}, "
                    f".clk({lid}_clk), .ena({lid}_ena), .aclrn({lid}_aclrn), .sclr({lid}_sclr), .sload({lid}_sload));")

        ram_mode = c["type"] == "MLAB" and s.get("RAM_DIS", "1") == "0"
        if ram_mode:
            # MLAB RAM write port (t0_mlab, FINDINGS M018): address bit k on F1 of ALM MLAB_WADDR_ALM[k],
            # clock/enable = LAB clock line 0 / enable 0
            wa = ", ".join(wire_of.get(f"{site}.{x}:F1", "1'b0") for x in reversed(MLAB_WADDR_ALM))
            body.append(f"wire [4:0] {lid}_waddr = {{{wa}}};")
        for a in alms:
            if f"LUT_MASK.{a}" not in s:
                s = dict(s)
                s[f"LUT_MASK.{a}"] = "0000000000000000"
            p = alm_params(s, a, c["type"])
            # unconnected LUT pins: tied 1 so the inverted LUT input (port value) is 0
            pins = ", ".join(f".{pn}({wire_of.get(f'{site}.{a}:{pn}', chr(49) + chr(39) + 'b1')})" for pn in PINS)
            outs = ", ".join(f".{o}({wire_of.get(f'{site}.{a}:{o}', '')})" for o in OUTS)
            # carry/share chain: cell a-1 -> a; cell 0 from LAB (x, y+1) cell 9 only when TTO_DIS=1,
            # cell 5 from cell 4 only when BTO_DIS=1 (measured: the "_DIS" bits connect; else cin = 0)
            x, y = int(site.split(".")[1]), int(site.split(".")[2])
            if a == 0:
                up = next((f"{b}.{x:03d}.{y + 1:03d}" for b in ("LAB", "MLAB") if f"{b}.{x:03d}.{y + 1:03d}" in cells), None)
                ok = s.get("TTO_DIS", "0") == "1"
                cin = (f"{ident(up)}_c9" if up else "1'b0") if ok else "1'b0"
                shin = (f"{ident(up)}_s9" if up else "1'b0") if ok else "1'b0"
            elif a == 5 and s.get("BTO_DIS", "0") != "1":
                cin, shin = "1'b0", "1'b0"
            else:
                cin, shin = f"{lid}_c{a - 1}", f"{lid}_s{a - 1}"
            body.append(f"rbf_alm #({', '.join(f'.{k}({v})' for k, v in p.items())}) {lid}_a{a} ({pins}, "
                        f".cin({cin}), .sharein({shin}), .clk({lid}_clk), .ena({lid}_ena), .aclrn({lid}_aclrn), "
                        f".sclr({lid}_sclr), .sload({lid}_sload), {outs}, .cout({lid}_c{a}), .shareout({lid}_s{a}), "
                        + (f".wclk({lid}_clk[0]), .we({lid}_ena[0]), .waddr({lid}_waddr));" if ram_mode
                           else ".wclk(1'b0), .we(1'b0), .waddr(5'b0));"))
        labs[site] = alms
    for site, c in cells.items():
        if c["type"] == "M10K":
            s = c["settings"]
            wa, wb = int(s.get("A_DATA_WIDTH", "40")), int(s.get("B_DATA_WIDTH", "1"))
            tdp = s.get("TRUE_DUAL_PORT", "0") == "1"
            extra = {k for k in s if not k.startswith("RAM.") and k not in M10K_KNOWN}
            if extra or (wa == 40) != (wb == 40) or (wa != wb and not tdp):
                unmodelled[f"M10K({wa}/{wb}{' tdp' if tdp else ''} {','.join(sorted(extra))})"] += 1
                continue
            init = 0
            for a in range(256):
                init |= (~int(s.get(f"RAM.{a}", "0").replace(".", ""), 16) & ((1 << 40) - 1)) << (40 * a)
            lid = ident(site)
            one = lambda pn: wire_of.get(f"{site}:{pn}", "1'b1")   # unconnected inputs read 1 (TDP ADDRB.11)
            bus = lambda name, n: "{" + ", ".join(one(f"{name}.{k}") for k in reversed(range(n))) + "}"
            wsel = lambda sel, w1, w0: one(w1 if s.get(sel, "0") == "1" else w0)
            on = lambda k, v="1": s.get(k) == v
            winv = lambda k: "~" if on(k) else ""
            ctl = {"CLKB1": int(on("BOT_1_CORECLK_SEL")), "OREGB": int(on("B_OUTPUT_SEL", "REG")),
                   "OCLK1": int(on("BOT_1_OUTCLK_SEL")), "OCLR": int(on("B_OUTCLR_EN", "REG")),
                   "REB": int(on("BOT_R_SEL")), "ENA": int(on("TOP_CORECLK_SEL")),
                   "CINVA": int(on("TOP_CLK_INV")), "CINVB": int(on("BOT_CLK_INV")),
                   "ENB": int(on("BOT_CORECLK_SEL") or on("BOT_INCLK_SEL")),
                   "OEN": int(on("TOP_OUTCLK_SEL") or on("BOT_OUTCLK_SEL"))}
            ctl["ENA"] = int(on("TOP_CORECLK_SEL") or on("TOP_INCLK_SEL"))
            # clock enable pins (t0_m10k_ctrl/ena, M022): CE0_SEL=1 -> ENABLE.1, else ENABLE.0
            c0en = wsel("TOP_CE0_SEL", "ENABLE.1", "ENABLE.0")
            c1en = wsel("BOT_CE0_SEL", "ENABLE.1", "ENABLE.0")
            body.append(f"wire [39:0] {lid}_qa; wire [19:0] {lid}_qb;")
            dia = ("{" + bus("DATABIN", 20)[1:-1] + ", " + bus("DATAAIN", 20)[1:-1] + "}") if wa == 40 else bus("DATAAIN", 40)
            body.append(f"rbf_m10k #(.PINIT(10240'h{init:02560x}), .WA({wa}), .WB({wb}), .TDP({int(tdp)}), "
                        + ", ".join(f".{k}({v})" for k, v in ctl.items()) + f") {lid}_ram "
                        f"(.clk0({one('CLKIN.0')}), .clk1({one('CLKIN.1')}), "
                        f".wea({winv('TOP_W_INV')}{wsel('TOP_W_SEL', 'WREN.1', 'WREN.0')}), "
                        f".web({winv('BOT_W_INV')}{wsel('BOT_W_SEL', 'WREN.0', 'WREN.1')}), "
                        f".enaa({c0en}), .enab({c1en}), .oena({c0en if on('TOP_OUTCLK_SEL') else c1en}), "
                        f".rdenb({one('RDEN.0')}), "
                        f".aclr({one('ACLR.0') if 'ACLR.0' in str([k for k in wire_of if k.startswith(site + ':')]) else chr(49) + chr(39) + 'b0'}), "
                        f".addra({bus('ADDRA', 12)}), "
                        f".addrb({bus('ADDRB', 12)}), .xa({one('DATAAIN.4') if wa == 1 else chr(49) + chr(39) + 'b0'}), "
                        f".xb({one('DATABIN.19') if wb == 1 else chr(49) + chr(39) + 'b0'}), .dia({dia}), "
                        f".dib({bus('DATABIN', 20)}), .doa({lid}_qa), .dob({lid}_qb));")
            # 40-bit mode: bits 0..19 on DATAAIN/DATAAOUT.0..19, bits 20..39 on DATABIN/DATABOUT.0..19 (M022)
            w40 = wb == 40 and not tdp
            for k in range(20):
                w = wire_of.get(f"{site}:DATAAOUT.{k}")
                if w:
                    assigns.append(f"assign {w} = {lid}_qa[{k}];")
                w = wire_of.get(f"{site}:DATABOUT.{k}")
                if w:
                    assigns.append(f"assign {w} = {lid}_qa[{k + 20}];" if w40 else f"assign {w} = {lid}_qb[{k}];")
        elif c["type"] == "DSP":
            s = c["settings"]
            known = {"ACC_INV", "ACLR0_SEL", "ACLR1_SEL", "BX_SIGNED", "BY_SIGNED", "CLK1_SEL", "CLK2_SEL",
                     "CLK_AX17_SEL", "CLK_AYZ17_SEL", "CLK_DYN_CTRL_SEL", "DEC_INV", "INREG_CTRL_BX",
                     "INREG_CTRL_BY", "INREG_CTRL_BZ", "OREG_CTRL", "PRELOAD_INV", "SUB_INV"}
            extra = {k for k in s if k not in known and not k.startswith("DATA_INV.")}
            used = {int(k.split(":")[0].split(".")[3]) for k in wire_of if k.startswith(site + ".") and ":DATAIN." in k}
            if extra or used - {0, 1, 2, 3, 5}:
                unmodelled[f"DSP({','.join(sorted(extra)) or 'inputs ' + str(sorted(used))})"] += 1
                continue
            lid = ident(site)
            def din(k, n):
                w = wire_of.get(f"{site}.{k}:DATAIN.{n}")
                if w:
                    return w
                inv = int(s.get(f"DATA_INV.{k}", "0"), 16) >> n & 1
                return "1'b0" if inv else "1'b1"     # unconnected: tied 1, DATA_INV makes it 0
            vec = lambda ks: "{" + ", ".join(din(k, n) for k in reversed(ks) for n in reversed(range(9))) + "}"
            prm = {"XS": int(s.get("BX_SIGNED", "0") == "1"), "YS": int(s.get("BY_SIGNED", "0") == "1"),
                   "INREG": int(s.get("INREG_CTRL_BX", "") == "REG"), "OREG": int(s.get("OREG_CTRL", "") == "REG")}
            body.append(f"wire [63:0] {lid}_r;")
            body.append(f"rbf_dsp18 #({', '.join(f'.{k}({v})' for k, v in prm.items())}) {lid}_dsp "
                        f"(.clk({wire_of.get(f'{site}:CLKIN.0', chr(49) + chr(39) + 'b0')}), .x({vec([2, 3])}), "
                        f".y({vec([0, 1])}), .result({lid}_r));")
            for k in range(64):
                w = wire_of.get(f"{site}:RESULT.{k}")
                if w:
                    assigns.append(f"assign {w} = {lid}_r[{k}];")
        elif c["type"] not in ("LAB", "MLAB", "GPIO"):
            unmodelled[c["type"]] += 0
    inputs, outputs = sorted(set(inputs)), sorted(set(outputs))
    # bidirectional pads (read and driven): the read side becomes a separate input port <pad>_in
    # (output enables are not modelled, so the pad is split into its two directions)
    both = set(inputs) & set(outputs)
    if both:
        inputs = sorted((f"{x}_in" if x in both else x) for x in inputs)
        pat = re.compile(r"(= ~?)(" + "|".join(sorted(both)) + r");$")
        assigns = [pat.sub(lambda m: f"{m.group(1)}{m.group(2)}_in;", a) for a in assigns]
    hdr = [f"// Generated by rbf2src/netlist/simv.py: behavioural model of the bitstream (not for synthesis)",
           f"// Unmodelled driver blocks (outputs undriven): {dict(unmodelled)}",
           f"module {top} ({', '.join(inputs + outputs)});"]
    hdr += [f"input {p};" for p in inputs] + [f"output {p};" for p in outputs]
    hdr += [f"wire {w};" for w in wires]
    return "\n".join(hdr + body + assigns + ["endmodule", ""]), dict(unmodelled)


def main(argv):
    top = "rbf_top"
    if "--top" in argv:
        top = argv[argv.index("--top") + 1]
        argv = [a for a in argv if a not in ("--top", top)]
    rbf, out = argv
    with tempfile.TemporaryDirectory() as td:
        bt = Path(td) / "a.bt"
        subprocess.run([str(btir.MCV), "decomp", btir.MODEL, rbf, str(bt)], check=True, capture_output=True)
        ir = btir.bt_to_ir(bt.read_text())
    text, um = emit(ir, top)
    Path(out).write_text(text)
    print(f"wrote {out}; unmodelled: {um}")


if __name__ == "__main__":
    main(sys.argv[1:])
