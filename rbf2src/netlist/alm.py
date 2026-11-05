#!/usr/bin/env python3
"""Physical Cyclone V ALM combinational model (Mistral settings + pin values).

The 64-bit LUT_MASK M holds four 4-LUTs L0..L3 (bits 16j..16j+15). Each is
addressed (bit0..bit3) by (A, B, x2, x3) and outputs the INVERTED stored bit:
    Lj = ~M[16j + A + 2B + 4x2 + 8x3]
  L0, L1 (top pair):    x2 = C, x3 = TA          (E_0: E0, F_0: TF, D_E/D_F: D)
  L2, L3 (bottom pair): x2 = D, x3 = BA          (E_1: E1, F_1: BF, C_E/C_F: C)
  TF/BF are the local feedback lines: Q of FF slot 1/2 (T_FEEDBACK_SEL 0/1) and 4/5
  (B_FEEDBACK_SEL 0/1) of the same ALM (Mistral docs lab-cell / lab-modes).
Outputs:
  split top    (TMODE E_0/F_0): comb0 = F0 ? L1 : L0
  split bottom (BMODE E_1/F_1): comb1 = F1 ? L3 : L2
  6-LUT output h (TMODE D_*/BMODE C_*): comb_h = e ? (f ? L3 : L2) : (f ? L1 : L0)
      with (e, f) from SIX_SEL[(TMODE, BMODE, h)]
  arithmetic (ARITH_SEL=ADDER), half h: u = L(2h) addressed (A,B,x2,TA/BA),
      v = ~L(2h+1) addressed (A,B,x2,F_h) (or the share line when SHARE), sum = u ^ v ^ cin.
  MLAB: LUT_MASK bits are permuted (lab_mask) before any of the above.
Unconnected pins read 0. Pins: A B C D E0 F0 E1 F1, plus feedback TF BF.
Everything here is checked against Quartus cyclonev_lcell_comb semantics by
almcheck.py over the corpus; classes it could not verify are listed in FINDINGS.
"""

X3_TOP = {"E_0": "E0", "F_0": "TF", "D_E": "D", "D_F": "D"}
X3_BOT = {"E_1": "E1", "F_1": "BF", "C_E": "C", "C_F": "C"}
SPLIT_SEL_TOP = {"E_0": "F0", "F_0": "F0"}
SPLIT_SEL_BOT = {"E_1": "F1", "F_1": "F1"}
# (TMODE, BMODE, half) -> (e pin, f pin) for 6-LUT outputs. f is the own half's F pin;
# e is the mode's TS/BS line: E0 (D_E) / TF (D_F) for the top, E1 (C_E) / BF (C_F) for the bottom.
# The other pair keeps its own TA/BA addressing, so with the other half in split mode
# this is Quartus' extended (7-input) LUT: the 7th input is the other pair's TA/BA pin.
SIX_SEL = {}
for tm in ("E_0", "F_0", "D_E", "D_F"):
    for bm in ("E_1", "F_1", "C_E", "C_F"):
        SIX_SEL[(tm, bm, 0)] = ("TF" if tm == "D_F" else "E0", "F0")
        SIX_SEL[(tm, bm, 1)] = ("BF" if bm == "C_F" else "E1", "F1")

# MLAB LUT bits are a permutation of the LAB layout: MLAB bit k holds LAB bit MLAB_PERM[k]
MLAB_PERM = [0, 1, 16, 17, 2, 3, 18, 19, 4, 5, 20, 21, 6, 7, 22, 23, 15, 14, 31, 30, 13, 12, 29, 28, 11, 10, 27, 26,
             9, 8, 25, 24, 32, 33, 48, 49, 34, 35, 50, 51, 36, 37, 52, 53, 38, 39, 54, 55, 47, 46, 63, 62, 45, 44, 61,
             60, 43, 42, 59, 58, 41, 40, 57, 56]


def lab_mask(m, block):
    """LUT_MASK in LAB bit order (identity for LAB, MLAB_PERM for MLAB)."""
    if block != "MLAB":
        return m
    out = 0
    for k, j in enumerate(MLAB_PERM):
        out |= ((m >> k) & 1) << j
    return out


def modes(settings, alm):
    return (settings.get(f"TMODE.{alm}", "D_E"), settings.get(f"BMODE.{alm}", "C_E"))


def sub_luts(m, tm, bm, p):
    """The four 4-LUT outputs for pin values p (dict pin -> 0/1)."""
    out = []
    for j in range(4):
        x2 = p.get("C", 0) if j < 2 else p.get("D", 0)
        x3 = p.get(X3_TOP[tm] if j < 2 else X3_BOT[bm], 0)
        k = 16 * j + p.get("A", 0) + 2 * p.get("B", 0) + 4 * x2 + 8 * x3
        out.append(1 - ((m >> k) & 1))
    return out


def comb(m, tm, bm, half, p, six_sel=None):
    L = sub_luts(m, tm, bm, p)
    six = tm.startswith("D_") if half == 0 else bm.startswith("C_")
    if not six:
        if half == 0:
            return L[1] if p.get(SPLIT_SEL_TOP[tm], 0) else L[0]
        return L[3] if p.get(SPLIT_SEL_BOT[bm], 0) else L[2]
    e, f = (six_sel or SIX_SEL)[(tm, bm, half)]
    lo = L[1] if p.get(f, 0) else L[0]
    hi = L[3] if p.get(f, 0) else L[2]
    return hi if p.get(e, 0) else lo


def adder_inputs(m, tm, bm, half, p):
    """Adder operands of half h: u = L(2h) on (A,B,x2,TA|BA), v = ~L(2h+1) on (A,B,x2,F_h)."""
    x2 = p.get("C", 0) if half == 0 else p.get("D", 0)
    ta = X3_TOP[tm] if half == 0 else X3_BOT[bm]
    f = "F0" if half == 0 else "F1"
    base = p.get("A", 0) + 2 * p.get("B", 0) + 4 * x2
    u = 1 - ((m >> (32 * half + base + 8 * p.get(ta, 0))) & 1)
    v = (m >> (32 * half + 16 + base + 8 * p.get(f, 0))) & 1
    return u, v
