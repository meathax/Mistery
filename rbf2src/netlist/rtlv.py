#!/usr/bin/env python3
"""Readable, synthesisable RTL Verilog from an RBF (no primitive library needed).

Starts from the behavioural netlist of simv.py (whose primitives co-simulate exactly with the original
designs, FINDINGS M011-M022) and folds every primitive instance into plain Verilog:
  * each ALM output becomes an `assign` with a minimised sum-of-products of its real input nets
    (truth table from the ALM semantics, Quine-McCluskey), adders become explicit sum/carry logic;
  * each used flip-flop becomes an `always` block with its clock, enable, clears and load folded
    from the LAB control settings (constants removed, inverted clocks as negedge);
  * MLAB RAM halves, M10K blocks (equal port widths) and DSP multipliers become reg arrays /
    multiplications with their decoded initial contents; M10K blocks with unequal port widths keep a
    behavioural model module, appended to the file so the output stays self-contained.
Nets are named after their driver (LAB_x_y_alm_PORT, M10K_x_y_DATABOUT_k, pad_<PIN> for package pins).
Usage: python rtlv.py RBF OUT.v [--top NAME]
"""
import re, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import btir, simv  # noqa: E402

# ----------------------------------------------------------------------------- parsing simv output


def split_top(s, sep=","):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


INST = re.compile(r"^(rbf_\w+) #\((.*)\) (\w+) \((.*)\);$")


def parse_inst(line):
    m = INST.match(line)
    if not m:
        return None
    typ, prm, name, ports = m.groups()
    params = {}
    for item in split_top(prm):
        k, v = re.match(r"\.(\w+)\((.*)\)$", item).groups()
        params[k] = v
    conns = {}
    for item in split_top(ports):
        k, v = re.match(r"\.(\w+)\((.*)\)$", item).groups()
        conns[k] = v
    return typ, params, name, conns


def num(v):
    """Verilog literal -> int (handles 64'h.., 3'b..., plain decimal)."""
    m = re.match(r"(\d+)'([hbd])([0-9a-fA-F_]+)$", v)
    if m:
        return int(m.group(3).replace("_", ""), {"h": 16, "b": 2, "d": 10}[m.group(2)])
    return int(v)


def const(v):
    """'1'b0' / '1'b1' -> 0 / 1, otherwise None (a net)."""
    return {"1'b0": 0, "1'b1": 1}.get(v)


def bus_items(v):
    """'{a, b, c}' -> [a, b, c] (MSB first); scalar -> [v]."""
    v = v.strip()
    return split_top(v[1:-1]) if v.startswith("{") else [v]

# ----------------------------------------------------------------------------- boolean helpers


def NOT(e):
    if e in ("1'b0", "1'b1"):
        return "1'b1" if e == "1'b0" else "1'b0"
    if e.startswith("~") and re.match(r"^~[\w\[\]]+$", e):
        return e[1:]
    return f"~{e}" if re.match(r"^[\w\[\]]+$", e) else f"~({e})"


def XOR_C(e, c):
    return NOT(e) if c else e


def qm(minterms, n):
    """Quine-McCluskey prime implicants + greedy cover; returns [(value, care_mask)]."""
    full = (1 << n) - 1
    terms = {(m, full) for m in minterms}
    primes = set()
    while terms:
        nxt, used = set(), set()
        tl = list(terms)
        by = {}
        for v, msk in tl:
            by.setdefault((msk, bin(v & msk).count("1")), []).append((v, msk))
        for (msk, ones), grp in by.items():
            for v2, m2 in by.get((msk, ones + 1), []):
                for v1, m1 in grp:
                    d = (v1 ^ v2) & msk
                    if d and not d & (d - 1):
                        nxt.add((v1 & ~d & msk, msk & ~d))
                        used.add((v1, m1))
                        used.add((v2, m2))
        primes |= terms - used
        terms = nxt
    left, cover = set(minterms), []
    primes = list(primes)
    while left:
        best = max(primes, key=lambda p: sum(1 for m in left if m & p[1] == p[0]))
        cover.append(best)
        left = {m for m in left if m & best[1] != best[0]}
    return cover


def sop(tt, names):
    """Truth table (int, bit j = f(index j)) over names (bit i of j = names[i]) -> Verilog expression."""
    n = len(names)
    size = 1 << n
    allb = (1 << size) - 1
    tt &= allb
    if tt == 0:
        return "1'b0"
    if tt == allb:
        return "1'b1"
    ones = [j for j in range(size) if tt >> j & 1]
    zeros = size - len(ones)
    if zeros < len(ones):           # fewer zeros: build the complement and invert
        return NOT(sop(allb ^ tt, names))
    prods = []
    for v, msk in sorted(qm(ones, n)):
        lits = [(names[i] if v >> i & 1 else NOT(names[i])) for i in range(n) if msk >> i & 1]
        prods.append(" & ".join(lits) if lits else "1'b1")
    if len(prods) == 1:
        return prods[0]
    return " | ".join(f"({p})" if " & " in p else p for p in prods)


class TT:
    """Bit-parallel truth-table arithmetic over a variable list."""

    def __init__(self, names):
        self.names = list(names)
        self.n = len(names)
        self.size = 1 << self.n
        self.all = (1 << self.size) - 1
        self.pat = []
        for i in range(self.n):
            p = 0
            for j in range(self.size):
                if j >> i & 1:
                    p |= 1 << j
            self.pat.append(p)

    def var(self, i):
        return self.pat[i]

    def c(self, b):
        return self.all if b else 0

    def inv(self, x):
        return self.all ^ x

    def mux(self, s, a, b):          # s ? b : a
        return (s & b) | (self.inv(s) & a)

    def support(self, tt):
        sup = []
        for i in range(self.n):
            step = 1 << i
            p0 = self.all ^ self.pat[i]
            if ((tt >> step) ^ tt) & p0:
                sup.append(i)
        return sup

    def expr(self, tt):
        sup = self.support(tt)
        if not sup:
            return "1'b1" if tt & 1 else "1'b0"
        # project onto the support variables
        size = 1 << len(sup)
        small = 0
        for j in range(size):
            idx = sum(1 << sup[k] for k in range(len(sup)) if j >> k & 1)
            if tt >> idx & 1:
                small |= 1 << j
        return sop(small, [self.names[i] for i in sup])

# ----------------------------------------------------------------------------- ALM


def alm_tables(P, src, T):
    """rbf_alm combinational semantics (sim/rbf_prims.v) as truth tables; src maps pin/q names to tables."""
    g = lambda k: src[k]
    M = num(P["MASK"])
    a, b, c, d = T.inv(g("A")), T.inv(g("B")), T.inv(g("C")), T.inv(g("D"))
    e0, f0, e1, f1 = T.inv(g("E0")), T.inv(g("F0")), T.inv(g("E1")), T.inv(g("F1"))
    TM, BM = int(P["TMODE"]), int(P["BMODE"])
    TF = T.inv(g("q2") if int(P["TFB"]) else g("q1"))
    BF = T.inv(g("q5") if int(P["BFB"]) else g("q4"))
    TA = e0 if TM == 0 else TF if TM == 1 else d
    BA = e1 if BM == 0 else BF if BM == 1 else c
    TS = TF if TM == 3 else e0
    BS = BF if BM == 3 else e1

    def lut4(m, i0, i1, i2, i3):
        out = 0
        for k in range(16):
            if not m >> k & 1:          # output is the inverted stored bit
                sel = T.all
                for bit, x in enumerate((i0, i1, i2, i3)):
                    sel &= x if k >> bit & 1 else T.inv(x)
                out |= sel
        return out
    M0, M1, M2, M3 = (M >> (16 * j) & 0xFFFF for j in range(4))
    L0, L1 = lut4(M0, a, b, c, TA), lut4(M1, a, b, c, TA)
    L2, L3 = lut4(M2, a, b, d, BA), lut4(M3, a, b, d, BA)
    comb0 = T.mux(TS, T.mux(f0, L0, L1), T.mux(f0, L2, L3)) if TM >= 2 else T.mux(f0, L0, L1)
    comb1 = T.mux(BS, T.mux(f1, L0, L1), T.mux(f1, L2, L3)) if BM >= 2 else T.mux(f1, L2, L3)
    v0f = T.inv(lut4(M1, a, b, c, f0))
    v1f = T.inv(lut4(M3, a, b, d, f1))
    return {"comb0": comb0, "comb1": comb1, "u0": L0, "u1": L2, "v0f": v0f, "v1f": v1f, "share0": L1, "share1": L3}


def maj(x, y, z):
    return f"({x} & {y}) | ({x} & {z}) | ({y} & {z})"


class Emitter:
    def __init__(self):
        self.decl, self.logic = [], []
        self.lab = {}           # lid -> dict of control expressions
        self.need_lib = set()

    # --- LAB control lines -------------------------------------------------------------------
    def labctl(self, P, C, name):
        lid = name[:-4]           # strip _ctl
        bits = lambda k, n: [num(P[k]) >> i & 1 for i in range(n)]
        g = lambda p: C[p]
        clka = g("din0") if int(P["CLKA_DIN"]) else g("clkin0")
        clkb = g("din1") if int(P["CLKB_DIN"]) else g("clkin1")
        cb, ci = bits("CLK_B", 3), bits("CLK_INV", 3)
        eu, ei, conn = bits("EN_USE", 3), bits("EN_INV", 3), bits("CONN", 6)
        ctl = {}
        for k in range(3):
            ctl[f"clk{k}"] = XOR_C(clkb if cb[k] else clka, ci[k])
        ens = [("din2", 0), ("din3", 1), ("din0", 2)]
        for pin, k in ens:
            ctl[f"ena{k}"] = XOR_C(g(pin), ei[k]) if eu[k] else "1'b1"
        a0g, a1g = int(P["ACLR0_GLB"]), int(P["ACLR1_GLB"])
        a0c = conn[4] if a0g else conn[3]
        a1c = conn[5] if a1g else conn[2]
        ctl["aclrn0"] = XOR_C(g("gaclr0") if a0g else g("din3"), int(P["ACLR0_INV"])) if a0c else "1'b1"
        ctl["aclrn1"] = XOR_C(g("gaclr1") if a1g else g("din2"), int(P["ACLR1_INV"])) if a1c else "1'b1"
        ctl["sclr"] = XOR_C(g("din1") if int(P["SCLR_DIN1"]) else g("din3"), int(P["SCLR_INV"])) if int(P["SCLR_ON"]) else "1'b0"
        ctl["sload"] = XOR_C(g("din2") if int(P["SLOAD_DIN2"]) else g("din1"), int(P["SLOAD_INV"])) if int(P["SLOAD_ON"]) else "1'b0"
        # fold constant pin values (unconnected LAB inputs are 1'b0 in simv)
        for k, v in ctl.items():
            ctl[k] = v.replace("~1'b0", "1'b1").replace("~1'b1", "1'b0")
        self.lab[lid] = ctl

    def ctl(self, lid, key):
        return self.lab[lid][key]

    # --- ALM ---------------------------------------------------------------------------------
    def alm(self, P, C, name, chain_needed):
        lid = name.rsplit("_a", 1)[0]
        outs = {o: C[o] for o in ("FFT0", "FFT1", "FFT1L", "FFB0", "FFB1", "FFB1L") if C.get(o)}
        cout_needed = C["cout"] in chain_needed
        share_needed = C["shareout"] in chain_needed
        ram, arith, share = int(P["RAM"]), int(P["ARITH"]), int(P["SHARE"])
        slots = {s: int(P[f"CK{s}"]) for s in (1, 2, 4, 5)}
        # which internal signals are needed
        nlut = {1: int(P["TDFF0"]), "1L": 0}
        uses_dl0 = any(slots[s] < 3 and not int(P[f"PK{s}"]) for s in (1, 2)) or \
            any(int(P[k]) and o in outs for k, o in (("TDFF0", "FFT0"), ("TDFF1", "FFT1"), ("TDFF1L", "FFT1L")))
        uses_dl1 = any(slots[s] < 3 and not int(P[f"PK{s}"]) for s in (4, 5)) or \
            any(int(P[k]) and o in outs for k, o in (("BDFF0", "FFB0"), ("BDFF1", "FFB1"), ("BDFF1L", "FFB1L")))
        used_q = {s for s in (1, 2, 4, 5) if slots[s] < 3}
        if not (outs or cout_needed or share_needed):
            return
        # variables: connected pins and FF feedback
        pins = ["A", "B", "C", "D", "E0", "F0", "E1", "F1"]
        names, src_pin = [], {}
        for pn in pins:
            v = C[pn]
            if const(v) is None:
                if v not in names:
                    names.append(v)
                src_pin[pn] = ("v", names.index(v))
            else:
                src_pin[pn] = ("c", const(v))
        qn = {s: f"{name}_q{s}" for s in (1, 2, 4, 5)}
        for s in sorted(used_q):
            names.append(qn[s])
            src_pin[f"q{s}"] = ("v", len(names) - 1)
        for s in (1, 2, 4, 5):
            src_pin.setdefault(f"q{s}", ("c", 0))
        T = TT(names)
        src = {k: (T.var(i) if kind == "v" else T.c(i)) for k, (kind, i) in src_pin.items()}
        tabs = alm_tables(P, src, T)
        w = lambda s: f"{name}_{s}"
        if ram:
            init = num(P["RAMINIT"])
            self.decl.append(f"reg [31:0] {w('memt')} = 32'h{init & 0xFFFFFFFF:08x}, {w('memb')} = 32'h{init >> 32:08x};")
            wclk = C["wclk"]
            self.logic.append(f"always @(posedge {self.vec(wclk)}) if ({self.vec(C['we'])}) begin "
                              f"{w('memt')}[{C['waddr']}] <= {C['E0']}; {w('memb')}[{C['waddr']}] <= {C['E1']}; end")
            ra = "{" + ", ".join(C[p] for p in ("F0", "D", "C", "B", "A")) + "}"
            dl0, dl1 = f"{w('memt')}[{ra}]", f"{w('memb')}[{ra}]"
        else:
            if arith or cout_needed:
                cin, shin = C["cin"], C["sharein"]
                for k in ("u0", "u1", "v0f", "v1f", "share0"):
                    self.logic.append(f"wire {w(k)} = {T.expr(tabs[k])};")
                v0 = shin if share else w("v0f")
                v1 = w("share0") if share else w("v1f")
                self.logic.append(f"wire {w('c0')} = {maj(w('u0'), v0, cin)};")
                if arith:
                    self.logic.append(f"wire {w('sum0')} = {w('u0')} ^ {v0} ^ {cin};")
                    self.logic.append(f"wire {w('sum1')} = {w('u1')} ^ {v1} ^ {w('c0')};")
                if cout_needed:
                    self.logic.append(f"assign {C['cout']} = {maj(w('u1'), v1, w('c0'))};")
            if share_needed:
                self.logic.append(f"assign {C['shareout']} = {T.expr(tabs['share1'])};")
            dl0 = w("sum0") if arith else None
            dl1 = w("sum1") if arith else None
            if not arith:
                if uses_dl0:
                    self.logic.append(f"wire {w('dl0')} = {T.expr(tabs['comb0'])};")
                    dl0 = w("dl0")
                if uses_dl1:
                    self.logic.append(f"wire {w('dl1')} = {T.expr(tabs['comb1'])};")
                    dl1 = w("dl1")
        # flip-flops
        sd0 = C["F1"] if int(P["TEF"]) else C["E0"]
        sd1 = C["F0"] if int(P["BEF"]) else C["E1"]
        for s in sorted(used_q):
            top = s in (1, 2)
            self.ff(lid, qn[s], slots[s], int(P["TCLR"] if top else P["BCLR"]), int(P[f"SC{s}"]),
                    int(P["TSL"] if top else P["BSL"]), int(P[f"PK{s}"]), dl0 if top else dl1, sd0 if top else sd1)
        omap = {"FFT0": ("TDFF0", 1, dl0), "FFT1": ("TDFF1", 2, dl0), "FFT1L": ("TDFF1L", 2, dl0),
                "FFB0": ("BDFF0", 4, dl1), "FFB1": ("BDFF1", 5, dl1), "FFB1L": ("BDFF1L", 5, dl1)}
        for o, net in outs.items():
            k, s, dl = omap[o]
            if int(P[k]):
                self.logic.append(f"assign {net} = {dl};")
            else:
                self.logic.append(f"assign {net} = {qn[s] if s in used_q else chr(49) + chr(39) + 'b0'};")

    def vec(self, v):
        """simv vector references X_clk[0] / X_ena[0] -> folded control expressions."""
        m = re.match(r"^(\w+)_(clk|ena)\[(\d)\]$", v)
        if m and m.group(1) in self.lab:
            return self.ctl(m.group(1), f"{m.group(2)}{m.group(3)}")
        return v

    def ff(self, lid, q, ck, clr, sc, sl, pk, dl, sd):
        clk = self.ctl(lid, f"clk{ck}")
        ena = self.ctl(lid, f"ena{ck}")
        aclrn = self.ctl(lid, f"aclrn{clr}")
        sclr = self.ctl(lid, "sclr") if sc else "1'b0"
        sload = self.ctl(lid, "sload") if sl else "1'b0"
        self.decl.append(f"reg {q} = 1'b0;")
        if const(clk) is not None:
            return                      # never clocked: holds its power-up value
        edge = f"negedge {clk[1:]}" if clk.startswith("~") else f"posedge {clk}"
        data = sd if pk else (dl or "1'b0")
        if const(sload) != 0:
            data = f"{sload} ? {sd} : {data}"
        if const(sclr) != 0:
            data = f"{sclr} ? 1'b0 : {data}"
        stmt = f"{q} <= {data};"
        if const(ena) != 1:
            stmt = f"if ({ena}) {stmt}"
        if const(aclrn) == 1:
            self.logic.append(f"always @({edge}) {stmt}")
        else:
            act = f"posedge {aclrn[1:]}" if aclrn.startswith("~") else f"negedge {aclrn}"
            cond = aclrn[1:] if aclrn.startswith("~") else f"!{aclrn}"
            self.logic.append(f"always @({edge} or {act}) if ({cond}) {q} <= 1'b0; else {stmt}")

    # --- M10K --------------------------------------------------------------------------------
    def m10k(self, P, C, name, line):
        wa, wb, tdp = int(P["WA"]), int(P["WB"]), int(P["TDP"])
        if wa != wb:
            self.need_lib.add("rbf_m10k")
            self.logic.append(line)
            return
        W = wa
        per = 1 if W == 40 else (32 // W if W <= 2 else 40 // W)
        depth = 256 * per
        S = {40: 4, 20: 3, 10: 2, 5: 1, 2: 0, 1: 0}[W]
        pinit = num(P["PINIT"])
        rows = [(pinit >> (40 * r)) & ((1 << 40) - 1) for r in range(256)]
        L = (0, 2, 1, 3)

        def phys(j, i):
            k = j * W + i
            kk = (k % 4) + 5 * ((k // 4) % 2) + 10 * (k // 8) if W <= 2 else k
            return 4 * (kk % 10) + L[kk // 10]
        words = [sum((rows[x // per] >> phys(x % per, i) & 1) << i for i in range(W)) for x in range(depth)]
        m = f"{name}_mem"
        self.decl.append(f"reg [{W - 1}:0] {m} [0:{depth - 1}];")
        # power-up contents: clear every word, then the non-zero ones
        self.decl.append(f"integer {name}_i;")
        self.decl.append(f"initial begin\n    for ({name}_i = 0; {name}_i < {depth}; {name}_i = {name}_i + 1) {m}[{name}_i] = {W}'h0;\n"
                         + "".join(f"    {m}[{x}] = {W}'h{v:x};\n" for x, v in enumerate(words) if v) + "end")

        def idx(addr, x):
            a = bus_items(addr)                     # MSB first: a[0] = ADDR.11
            hi = a[:12 - S]
            return "{" + ", ".join(hi + ([x] if W == 1 else [])) + "}"
        ia, ib = idx(C["addra"], C["xa"]), idx(C["addrb"], C["xb"])
        dia = bus_items(C["dia"])[::-1][:W]        # LSB first
        dib = bus_items(C["dib"])[::-1][:W] if W <= 20 else []
        da = "{" + ", ".join(dia[::-1]) + "}"
        db = "{" + ", ".join(dib[::-1]) + "}" if dib else None
        ck = lambda inv, sig: f"negedge {sig}" if inv else f"posedge {sig}"
        clka = ck(int(P["CINVA"]), C["clk0"])
        clkb = ck(int(P["CINVB"]), C["clk1"] if int(P["CLKB1"]) else C["clk0"])
        clko = f"posedge {C['clk1'] if int(P['OCLK1']) else C['clk0']}"
        wea = C["wea"] if not int(P["ENA"]) else f"({C['wea']}) & ({C['enaa']})"
        rden = " & ".join(x for x, on in ((C["rdenb"], int(P["REB"])), (C["enab"], int(P["ENB"]))) if on) or "1'b1"
        ra, rb, ro, rbo = (f"{name}_{s}" for s in ("ra", "rb", "ro", "rbo"))
        self.decl.append(f"reg [{W - 1}:0] {ra} = {W}'h0, {rb} = {W}'h0, {ro} = {W}'h0, {rbo} = {W}'h0;")
        body_a = f"if ({wea}) {m}[{ia}] <= {da};"
        if tdp:
            body_a = f"{ra} <= ({wea}) ? {da} : {m}[{ia}]; " + body_a
        self.logic.append(f"always @({clka}) begin {body_a} end")
        b40 = (not tdp and W == 40)
        tgt = ra if b40 else rb
        rd = f"{tgt} <= {m}[{ib}];" if not (tdp and db) else f"{tgt} <= ({C['web']}) ? {db} : {m}[{ib}];"
        body_b = rd if rden == "1'b1" else f"if ({rden}) {rd}"
        if tdp and db:
            body_b += f" if ({C['web']}) {m}[{ib}] <= {db};"
        self.logic.append(f"always @({clkb}) begin {body_b} end")
        oreg = int(P["OREGB"])
        if oreg:
            oen = C["oena"] if int(P["OEN"]) else None
            upd = f"begin {ro} <= {ra}; {rbo} <= {rb}; end"
            upd = f"if ({oen}) {upd}" if oen else upd
            if int(P["OCLR"]):
                self.logic.append(f"always @({clko} or posedge {C['aclr']}) if ({C['aclr']}) begin {ro} <= {W}'h0; "
                                  f"{rbo} <= {W}'h0; end else {upd}")
            else:
                self.logic.append(f"always @({clko}) {upd}")
        qa = (ro if (oreg and b40) else ra)
        qb = (rbo if oreg else rb)
        pad = lambda x, n: f"{{{n - W}'b0, {x}}}" if n > W else x
        self.logic.append(f"assign {C['doa']} = {pad(qa, 40)};")
        if W <= 20:
            self.logic.append(f"assign {C['dob']} = {pad(qb, 20)};")

    # --- DSP ---------------------------------------------------------------------------------
    def dsp(self, P, C, name):
        xs, ys, inreg, oreg = int(P["XS"]), int(P["YS"]), int(P["INREG"]), int(P["OREG"])
        x, y, r = f"{name}_x", f"{name}_y", f"{name}_p"
        clk = C["clk"]
        ext = lambda v, s: f"{{{v}[17], {v}}}" if s else f"{{1'b0, {v}}}"
        if inreg:
            self.decl.append(f"reg [17:0] {x} = 18'h0, {y} = 18'h0;")
            self.logic.append(f"always @(posedge {clk}) begin {x} <= {C['x']}; {y} <= {C['y']}; end")
        else:
            self.logic.append(f"wire [17:0] {x} = {C['x']}, {y} = {C['y']};")
        prod = f"$signed({ext(x, xs)}) * $signed({ext(y, ys)})"
        if oreg:
            self.decl.append(f"reg signed [63:0] {r} = 64'sh0;")
            self.logic.append(f"always @(posedge {clk}) {r} <= {prod};")
        else:
            self.logic.append(f"wire signed [63:0] {r} = {prod};")
        self.logic.append(f"assign {C['result']} = {r};")


def lib_text(names):
    """Behavioural model modules still referenced (fallbacks), copied from sim/rbf_prims.v."""
    txt = (btir.RES / "sim" / "rbf_prims.v").read_text()
    out = []
    for n in sorted(names):
        m = re.search(r"(module " + n + r"\b.*?endmodule)", txt, re.S)
        out.append(m.group(1))
    return "\n\n".join(out)


def to_rtl(simv_text, ir, top):
    em = Emitter()
    lines = simv_text.splitlines()
    insts = [(i, parse_inst(l)) for i, l in enumerate(lines)]
    # chain wires used by an adder/shared ALM (only those need cout/shareout logic)
    chain = set()
    for _, p in insts:
        if p and p[0] == "rbf_alm":
            P, C = p[1], p[3]
            if int(P["ARITH"]):
                chain.add(C["cin"])
            if int(P["SHARE"]):
                chain.add(C["sharein"])
    # transitive: an ALM whose cout feeds the chain needs its own cin (and sharein when shared),
    # even when it is not an adder itself (carry pass-through)
    alms = [p for _, p in insts if p and p[0] == "rbf_alm"]
    grew = True
    while grew:
        grew = False
        for _, P, _n, C in alms:
            if C["cout"] in chain:
                for k in (["cin", "sharein"] if int(P["SHARE"]) else ["cin"]):
                    if C[k] not in chain:
                        chain.add(C[k])
                        grew = True
    for _, p in insts:                       # LAB controls first (ALMs refer to them)
        if p and p[0] == "rbf_labctl":
            em.labctl(p[1], p[3], p[2])
    keep = []
    for i, p in insts:
        line = lines[i]
        if p is None:
            if re.match(r"^wire \[2:0\] \w+_clk, \w+_ena; wire \[1:0\]", line):
                continue                    # LAB control vectors are folded away
            keep.append(line)
            continue
        typ, P, name, C = p
        if typ == "rbf_labctl":
            continue
        if typ == "rbf_alm":
            em.alm(P, C, name, chain)
        elif typ == "rbf_m10k":
            em.m10k(P, C, name, line)
        elif typ == "rbf_dsp18":
            em.dsp(P, C, name)
        else:
            em.need_lib.add(typ)
            em.logic.append(line)
    # assemble: header/ports/wires from simv, then declarations and logic, then simv assigns
    out, tail = [], []
    for line in keep:
        if line.startswith("endmodule"):
            continue
        if line.startswith("assign "):
            tail.append(line)
        elif line.startswith("// Generated by"):
            out.append("// Generated by rbf2src/netlist/rtlv.py from the RBF: synthesisable RTL (flattened gate level).")
            out.append("// LUTs are minimised boolean logic, FFs always blocks, RAMs reg arrays with their decoded contents.")
            out.append("// Net names follow their hardware driver; pad_<PIN> are package pins, root_* clock-network inputs.")
        else:
            out.append(line)
    text = "\n".join(out + em.decl + em.logic + tail + ["endmodule", ""])
    if em.need_lib:
        text += "\n// Behavioural models for blocks not flattened yet\n" + lib_text(em.need_lib) + "\n"
    return rename(text, ir), em


def rename(text, ir):
    """n<i> -> driver-based net names."""
    names = {}
    used = set()
    for i, net in enumerate(ir["nets"]):
        d = net["driver"]
        base = simv.ident(d) if ":" in d else "clk_" + simv.ident(d)
        nm = base
        k = 1
        while nm in used:
            nm = f"{base}_{k}"
            k += 1
        used.add(nm)
        names[f"n{i}"] = nm
    return re.sub(r"\bn(\d+)\b", lambda m: names.get(m.group(0), m.group(0)), text)


def blackboxes(ir, simv_text):
    """Blocks with no model yet (PLLs, clock muxes, HPS/HMC interfaces, undecoded M10K/DSP modes):
    one instance per block with all its decoded settings as string parameters and every connected
    net wired to a port, plus an empty module stub per block type so the file compiles. Replace a
    stub with the matching Intel IP / atom to make that block functional."""
    modelled = set(re.findall(r"^rbf_(?:m10k|dsp18) #\(.*\) (\w+?)_(?:ram|dsp) \(", simv_text, re.M))
    ports = {}                                  # site -> {port: (direction, net index)}
    for i, net in enumerate(ir["nets"]):
        d = net["driver"]
        for end, direction in [(d, "output")] + [(s, "input") for s in net["sinks"]]:
            if ":" not in end:
                continue
            blk, pn = end.split(":")
            f = blk.split(".")
            site = ".".join(f[:3])
            typ = ir["cells"][site]["type"] if site in ir["cells"] else f[0]   # HPS blocks have no settings
            if typ in ("LAB", "MLAB", "GPIO") or (typ in ("M10K", "DSP") and simv.ident(site) in modelled):
                continue
            if typ == "HMC" and direction == "input" and "DOUT" in pn:
                continue                        # HMC IOINT bypass outputs are package pins (pad_*)
            port = simv.ident((".".join(f[3:]) + "_" if len(f) > 3 else "") + pn)
            if port[0].isdigit():
                port = "p" + port                # Verilog identifiers cannot start with a digit
            ports.setdefault(site, {})[port] = (direction, i)
    if not ports:
        return "", ""
    inst, stubs = [], {}
    for site in sorted(ports):
        c = ir["cells"].get(site, {"type": site.split(".")[0], "settings": {}})
        typ = "RBF_" + simv.ident(c["type"])
        prm = {simv.ident(k): str(v).replace('"', "") for k, v in sorted(c["settings"].items())}
        st = stubs.setdefault(typ, ({}, {}))
        st[0].update({k: "" for k in prm})
        for pn, (d, _) in ports[site].items():
            st[1][pn] = d
        ps = ", ".join(f'.{k}("{v}")' for k, v in prm.items())
        cs = ", ".join(f".{pn}(n{i})" for pn, (d, i) in sorted(ports[site].items()))
        inst.append(f"{typ} {'#(' + ps + ') ' if ps else ''}{simv.ident(site)} ({cs});")
    lib = []
    for typ, (prm, pts) in sorted(stubs.items()):
        head = f"// Black box: {typ[4:]} has no behavioural model yet; replace with the Intel atom/IP.\n"
        par = ("#(" + ", ".join(f'parameter {k} = ""' for k in sorted(prm)) + ") ") if prm else ""
        lib.append(head + f"module {typ} " + par + "(" + ", ".join(f"{d} {pn}" for pn, d in sorted(pts.items()))
                   + f");\nendmodule")
    return "\n".join(inst), "\n\n".join(lib)


def emit(ir, top="rbf_top"):
    text, unm = simv.emit(ir, top)
    bb_inst, bb_lib = blackboxes(ir, text)
    if bb_inst:
        text = text.replace("\nendmodule\n", "\n// Blocks without a model yet, wired as black boxes\n" + bb_inst
                            + "\nendmodule\n", 1)
    rtl, em = to_rtl(text, ir, top)
    if bb_lib:
        rtl = (rtl.rstrip("\n") + "\n\n// ---- Black-box stubs (no behaviour; replace to make these blocks work) ----\n"
               + bb_lib + "\n")
        rtl = rename(rtl, ir)
    return rtl, unm


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
