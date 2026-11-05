// Behavioural models of Cyclone V LAB/MLAB logic as decoded from the bitstream.
// Semantics follow rbf2src/netlist/alm.py (LUT, verified vs cyclonev_lcell_comb) and
// rbf2src/netlist/ffcheck.py (FF controls, verified vs Quartus dffeas); FINDINGS M011-M016.
// Simulation only. FFs power up 0 (configuration clears all registers).
`timescale 1ps/1ps

module rbf_alm #(
    parameter [63:0] MASK = 64'h0,  // LUT_MASK in LAB bit order (MLAB already permuted)
    parameter TMODE = 2,            // 0 E_0, 1 F_0, 2 D_E, 3 D_F
    parameter BMODE = 2,            // 0 E_1, 1 F_1, 2 C_E, 3 C_F
    parameter ARITH = 0,            // ARITH_SEL = ADDER
    parameter SHARE = 0,            // adder second operand from the share chain
    parameter TFB = 0, BFB = 0,     // [TB]_FEEDBACK_SEL
    parameter TDFF0 = 0, TDFF1 = 0, TDFF1L = 0, BDFF0 = 0, BDFF1 = 0, BDFF1L = 0, // 1 = NLUT
    parameter PK1 = 0, PK2 = 0, PK4 = 0, PK5 = 0,  // packed: D from sdata
    parameter TEF = 0, BEF = 0,     // sdata: top E0 (0) / F1 (1), bottom E1 (0) / F0 (1)
    parameter CK1 = 3, CK2 = 3, CK4 = 3, CK5 = 3,  // clock line 0..2, 3 = none
    parameter TCLR = 0, BCLR = 0,   // ACLR line of each half
    parameter SC1 = 0, SC2 = 0, SC4 = 0, SC5 = 0,  // sync clear reaches this FF
    parameter TSL = 0, BSL = 0,     // sync load reaches this half
    parameter RAM = 0,              // MLAB RAM mode: halves read 32x1 memories at {F0,D,C,B,A}
    parameter [63:0] RAMINIT = 64'h0 // initial contents, top half [31:0], bottom half [63:32]
) (
    input A, B, C, D, E0, F0, E1, F1,
    input wclk, input we, input [4:0] waddr,  // MLAB RAM write port (LAB-wide)
    input cin, sharein,
    input [2:0] clk, input [2:0] ena, input [1:0] aclrn, input sclr, input sload,
    output FFT0, FFT1, FFT1L, FFB0, FFB1, FFB1L,
    output cout, shareout
);
    reg q1 = 1'b0, q2 = 1'b0, q4 = 1'b0, q5 = 1'b0;
    // LUT inputs pass through inverters (Quartus shows every lcell data input as !net); the
    // model's LUT works on these inverted values ("port values"). Unconnected pins are tied
    // to 1 by the emitter, i.e. port value 0. The sdata path (E/F into the FFs) is not inverted.
    wire a = ~A, b = ~B, c = ~C, d = ~D, e0 = ~E0, f0 = ~F0, e1 = ~E1, f1 = ~F1;
    wire TF = ~(TFB ? q2 : q1);
    wire BF = ~(BFB ? q5 : q4);
    wire TA = (TMODE == 0) ? e0 : (TMODE == 1) ? TF : d;
    wire BA = (BMODE == 0) ? e1 : (BMODE == 1) ? BF : c;
    wire TS = (TMODE == 3) ? TF : e0;    // 6-LUT select (D_E: E0, D_F: TF)
    wire BS = (BMODE == 3) ? BF : e1;
    function lut4(input [15:0] m, input a, input b, input c, input d);
        lut4 = ~m[{d, c, b, a}];
    endfunction
    wire [15:0] M0 = MASK[15:0], M1 = MASK[31:16], M2 = MASK[47:32], M3 = MASK[63:48];
    wire L0 = lut4(M0, a, b, c, TA), L1 = lut4(M1, a, b, c, TA);
    wire L2 = lut4(M2, a, b, d, BA), L3 = lut4(M3, a, b, d, BA);
    wire six0 = (TMODE >= 2), six1 = (BMODE >= 2);
    wire comb0 = six0 ? (TS ? (f0 ? L3 : L2) : (f0 ? L1 : L0)) : (f0 ? L1 : L0);
    wire comb1 = six1 ? (BS ? (f1 ? L3 : L2) : (f1 ? L1 : L0)) : (f1 ? L3 : L2);
    // adder: u = L(2h) on TA/BA, v = ~L(2h+1) on F_h, share line = L(2h+1) on TA/BA
    wire u0 = L0, u1 = L2;
    wire v0f = ~lut4(M1, a, b, c, f0), v1f = ~lut4(M3, a, b, d, f1);
    wire share0 = L1, share1 = L3;
    wire v0 = SHARE ? sharein : v0f;
    wire v1 = SHARE ? share0 : v1f;
    wire sum0 = u0 ^ v0 ^ cin;
    wire c0 = (u0 & v0) | (u0 & cin) | (v0 & cin);
    wire sum1 = u1 ^ v1 ^ c0;
    assign cout = (u1 & v1) | (u1 & c0) | (v1 & c0);
    assign shareout = share1;
    // MLAB RAM: each half stores 32 words written from its E pin (E0 top, E1 bottom) at the
    // LAB write address; asynchronous read, so the ALM FFs give a registered read port.
    reg [31:0] memt = RAMINIT[31:0], memb = RAMINIT[63:32];
    wire [4:0] raddr = {F0, D, C, B, A};
    always @(posedge wclk) if (RAM && we) begin memt[waddr] <= E0; memb[waddr] <= E1; end
    wire dl0 = RAM ? memt[raddr] : ARITH ? sum0 : comb0;
    wire dl1 = RAM ? memb[raddr] : ARITH ? sum1 : comb1;
    wire sd0 = TEF ? F1 : E0;
    wire sd1 = BEF ? F0 : E1;
    // one FF: posedge of its clock line, async clear active low, then ena / sclr / sload / D
    `define RBF_FF(Q, CK, CLRL, SC, SL, PKD, DL, SD) \
        always @(posedge clk[CK] or negedge aclrn[CLRL]) \
            if (!aclrn[CLRL]) Q <= 1'b0; \
            else if (ena[CK]) Q <= (SC && sclr) ? 1'b0 : (SL && sload) ? SD : (PKD ? SD : DL);
    generate
        if (CK1 < 3) begin : g_ff1 `RBF_FF(q1, CK1, TCLR, SC1, TSL, PK1, dl0, sd0) end
        if (CK2 < 3) begin : g_ff2 `RBF_FF(q2, CK2, TCLR, SC2, TSL, PK2, dl0, sd0) end
        if (CK4 < 3) begin : g_ff4 `RBF_FF(q4, CK4, BCLR, SC4, BSL, PK4, dl1, sd1) end
        if (CK5 < 3) begin : g_ff5 `RBF_FF(q5, CK5, BCLR, SC5, BSL, PK5, dl1, sd1) end
    endgenerate
    `undef RBF_FF
    assign FFT0 = TDFF0 ? dl0 : q1;
    assign FFT1 = TDFF1 ? dl0 : q2;
    assign FFT1L = TDFF1L ? dl0 : q2;
    assign FFB0 = BDFF0 ? dl1 : q4;
    assign FFB1 = BDFF1 ? dl1 : q5;
    assign FFB1L = BDFF1L ? dl1 : q5;
endmodule

// LAB-wide control lines (Mistral lab-common, with the measured label swaps of FINDINGS M015)
module rbf_labctl #(
    parameter CLKA_DIN = 0, CLKB_DIN = 0,          // CLKA_SEL/CLKB_SEL = DIN
    parameter [2:0] CLK_B = 3'b000,                // per line: clkb (bit named CLKk_INV)
    parameter [2:0] CLK_INV = 3'b000,              // per line: inverted (bit named CLKk_SEL)
    parameter [2:0] EN_USE = 3'b111, EN_INV = 3'b111,
    parameter ACLR0_GLB = 0, ACLR1_GLB = 0, ACLR0_INV = 0, ACLR1_INV = 0,
    parameter [5:0] CONN = 6'b0,                   // connected: ACLR.1 ACLR.0 DATAIN.3 .2 .1 .0
    parameter SCLR_ON = 1, SCLR_DIN1 = 0, SCLR_INV = 0,
    parameter SLOAD_ON = 1, SLOAD_DIN2 = 0, SLOAD_INV = 0
) (
    input clkin0, clkin1, din0, din1, din2, din3, gaclr0, gaclr1,
    output [2:0] clk, output [2:0] ena, output [1:0] aclrn, output sclr, output sload
);
    wire clka = CLKA_DIN ? din0 : clkin0;
    wire clkb = CLKB_DIN ? din1 : clkin1;
    genvar k;
    generate for (k = 0; k < 3; k = k + 1) begin : l
        assign clk[k] = (CLK_B[k] ? clkb : clka) ^ CLK_INV[k];
    end endgenerate
    assign ena[0] = EN_USE[0] ? din2 ^ EN_INV[0] : 1'b1;
    assign ena[1] = EN_USE[1] ? din3 ^ EN_INV[1] : 1'b1;
    assign ena[2] = EN_USE[2] ? din0 ^ EN_INV[2] : 1'b1;
    wire a0c = ACLR0_GLB ? CONN[4] : CONN[3];
    wire a1c = ACLR1_GLB ? CONN[5] : CONN[2];
    assign aclrn[0] = a0c ? ((ACLR0_GLB ? gaclr0 : din3) ^ ACLR0_INV) : 1'b1;
    assign aclrn[1] = a1c ? ((ACLR1_GLB ? gaclr1 : din2) ^ ACLR1_INV) : 1'b1;
    assign sclr = SCLR_ON ? ((SCLR_DIN1 ? din1 : din3) ^ SCLR_INV) : 1'b0;
    assign sload = SLOAD_ON ? ((SLOAD_DIN2 ? din2 : din1) ^ SLOAD_INV) : 1'b0;
endmodule

// M10K block (FINDINGS M017, M020). The array is 256 rows x 40 physical bits (PINIT = decoded true
// values, row r at PINIT[40r +: 40]). A port of width W addresses row ADDR[11:4] and sub-word
// j = ADDR[3:S] (x1: {ADDR[3:0], XA}); logical bit i is flat bit k = j*W + i, placed at physical
// bit 4*(K mod 10) + {0,2,1,3}[K div 10] with K = k (W >= 5) or the 32-of-40 packing
// K = (k mod 4) + 5*((k div 4) mod 2) + 10*(k div 8) (W <= 2). Port A writes; port B writes only
// in true dual-port mode. Registered addresses; same-port read-during-write returns the new data
// (TDP), mixed-port returns old data. Read data: port B on DOB (40-bit mode: on DOA), port A on DOA.
module rbf_m10k #(parameter [10239:0] PINIT = 10240'b0, parameter integer WA = 40, WB = 40, TDP = 0,
                  parameter CLKB1 = 0,      // port B (address/read) on clk1 (BOT_1_CORECLK_SEL)
                  parameter OREGB = 0,      // port B output register (B_OUTPUT_SEL=REG)
                  parameter OCLK1 = 0,      // output register on clk1 (BOT_1_OUTCLK_SEL)
                  parameter OCLR = 0,       // output register async clear from aclr (B_OUTCLR_EN=REG)
                  parameter REB = 0,        // port B read enable from rdenb (BOT_R_SEL)
                  parameter ENA = 0,        // port A clock enable from enaa (TOP_CORECLK_SEL)
                  parameter CINVA = 0, CINVB = 0,     // inverted port clocks (TOP_/BOT_CLK_INV)
                  parameter ENB = 0,        // port B clock enable from enab (BOT_CORECLK_SEL / BOT_INCLK_SEL)
                  parameter OEN = 0) (      // output register clock enable from oena (TOP_/BOT_OUTCLK_SEL)
    input clk0, input clk1, input wea, input web, input enaa, input enab, input oena, input rdenb, input aclr,
    input [11:0] addra, input [11:0] addrb, input xa, input xb,
    input [39:0] dia, input [19:0] dib, output [39:0] doa, output [19:0] dob
);
    reg [39:0] mem [0:255];
    reg [39:0] ra = 40'b0, ro = 40'b0;
    reg [19:0] rb = 20'b0, rbo = 20'b0;
    integer r, i;
    initial for (r = 0; r < 256; r = r + 1) mem[r] = PINIT[40 * r +: 40];
    function integer phys(input integer w, input integer j, input integer ii);
        integer k, kk;
        begin
            k = j * w + ii;
            kk = (w <= 2) ? (k % 4) + 5 * ((k / 4) % 2) + 10 * (k / 8) : k;
            phys = 4 * (kk % 10) + ((kk / 10) == 0 ? 0 : (kk / 10) == 1 ? 2 : (kk / 10) == 2 ? 1 : 3);
        end
    endfunction
    function integer sub(input integer w, input [11:0] a, input x);
        sub = (w == 40) ? 0 : (w == 20) ? a[3] : (w == 10) ? a[3:2] : (w == 5) ? a[3:1] :
              (w == 2) ? a[3:0] : {a[3:0], x};
    endfunction
    wire clka = clk0 ^ CINVA;
    wire clkb = (CLKB1 ? clk1 : clk0) ^ CINVB;
    wire clko = OCLK1 ? clk1 : clk0;
    wire wa_en = wea && (!ENA || enaa);
    always @(posedge clka) begin        // port A: write, TDP read (same-port new data)
        if (TDP) for (i = 0; i < 40; i = i + 1)
            if (i < WA) ra[i] <= wa_en ? dia[i] : mem[addra[11:4]][phys(WA, sub(WA, addra, xa), i)];
        if (wa_en) for (i = 0; i < 40; i = i + 1)
            if (i < WA) mem[addra[11:4]][phys(WA, sub(WA, addra, xa), i)] <= dia[i];
    end
    always @(posedge clkb) begin        // port B: read (mixed-port old data), TDP write
        if (!TDP && WB == 40) begin
            if ((!REB || rdenb) && (!ENB || enab)) for (i = 0; i < 40; i = i + 1) ra[i] <= mem[addrb[11:4]][phys(40, 0, i)];
        end else if ((!REB || rdenb) && (!ENB || enab)) begin
            for (i = 0; i < 20; i = i + 1)
                if (i < WB) rb[i] <= (TDP && web) ? dib[i] : mem[addrb[11:4]][phys(WB, sub(WB, addrb, xb), i)];
        end
        if (TDP && web) for (i = 0; i < 20; i = i + 1)
            if (i < WB) mem[addrb[11:4]][phys(WB, sub(WB, addrb, xb), i)] <= dib[i];
    end
    always @(posedge clko or posedge aclr)
        if (OCLR && aclr) begin ro <= 40'b0; rbo <= 20'b0; end
        else if (!OEN || oena) begin ro <= ra; rbo <= rb; end
    assign doa = (OREGB && !TDP && WB == 40) ? ro : ra;
    assign dob = OREGB ? rbo : rb;
endmodule

// DSP block, 18x18 multiply only (t0_dsp, FINDINGS M019): Y = DATAIN of sub-blocks 0..1, X = sub-blocks
// 2..3 (9 bits each, little-endian); optional input and output registers on CLKIN.0; RESULT = X*Y.
module rbf_dsp18 #(parameter XS = 1, YS = 1, INREG = 1, OREG = 1) (
    input clk, input [17:0] x, input [17:0] y, output [63:0] result
);
    reg [17:0] rx = 18'b0, ry = 18'b0;
    reg [63:0] rp = 64'b0;
    wire [17:0] ix = INREG ? rx : x, iy = INREG ? ry : y;
    wire signed [18:0] sx = {XS ? ix[17] : 1'b0, ix}, sy = {YS ? iy[17] : 1'b0, iy};
    wire signed [63:0] prod = sx * sy;
    always @(posedge clk) begin rx <= x; ry <= y; rp <= prod; end
    assign result = OREG ? rp : prod;
endmodule
