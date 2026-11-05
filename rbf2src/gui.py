#!/usr/bin/env python3
"""Mistery GUI: pick an RBF, pick an output folder, press Convert.

Wraps convert.py (no extra packages: tkinter ships with Python). Run: python rbf2src/gui.py
"""
import os, queue, subprocess, sys, threading, time, traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import convert  # noqa: E402
from hacker_art import HackerArt  # noqa: E402

TITLE = "Mistery"
VERSION = "v1.0"

# scene palette
BG, PANEL, EDGE = "#030603", "#07100a", "#1d4d1d"
GREEN, CYAN, MAGENTA, DIM, WHITE = "#39ff14", "#00dcff", "#ff00aa", "#2f8f2f", "#d8ffd0"
MONO = ("Consolas", 10)
MONO_B = ("Consolas", 10, "bold")

BANNER = [
    "███╗   ███╗██╗███████╗████████╗███████╗██████╗ ██╗   ██╗",
    "████╗ ████║██║██╔════╝╚══██╔══╝██╔════╝██╔══██╗╚██╗ ██╔╝",
    "██╔████╔██║██║███████╗   ██║   █████╗  ██████╔╝ ╚████╔╝ ",
    "██║╚██╔╝██║██║╚════██║   ██║   ██╔══╝  ██╔══██╗  ╚██╔╝  ",
    "██║ ╚═╝ ██║██║███████║   ██║   ███████╗██║  ██║   ██║   ",
    "╚═╝     ╚═╝╚═╝╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝   ╚═╝   ",
]
BANNER_COLOURS = ["#39ff14", "#2cf05a", "#1fe08f", "#12d0c0", "#00dcff", "#ff00aa"]


OPTIONS = (("rtl", True, "RTL VERILOG", "synthesisable hdl ........ _rtl.v"),
           ("source", True, "REBUILDABLE SOURCE", "site netlist + sidecar .. .v .sidecar.json"),
           ("sim", True, "SIM MODEL", "behavioural model ....... _sim.v"),
           ("verify", True, "VERIFY", "rebuild rbf, byte compare"),
           ("ir", False, "NETLIST JSON", "decoded netlist (large) . .ir.json"))


def resource(name):
    """Bundled file in the frozen app, else from rbf2src/packaging."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "packaging"
    return base / name


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{TITLE} :: RBF -> SOURCE")
        self.configure(bg=BG)
        self.minsize(1100, 800)
        self.geometry("1180x880")
        try:
            self.iconbitmap(str(resource("mistery.ico")))
        except Exception:
            pass
        self.rbf = tk.StringVar()
        self.out = tk.StringVar()
        self.opt = {k: tk.BooleanVar(value=v) for k, v, _, _ in OPTIONS}
        self.msgs = queue.Queue()
        self.worker = None
        self._style()
        self._build()
        self.after(100, self._pump)

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=GREEN, font=MONO, bordercolor=EDGE,
                    lightcolor=EDGE, darkcolor=EDGE, troughcolor=PANEL, focuscolor=CYAN)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=GREEN)
        s.configure("Dim.TLabel", background=BG, foreground=DIM)
        s.configure("Panel.TLabel", background=PANEL, foreground=GREEN)
        s.configure("PanelDim.TLabel", background=PANEL, foreground=DIM)
        s.configure("Status.TLabel", background=BG, foreground=CYAN, font=MONO_B)
        s.configure("TEntry", fieldbackground="#000000", foreground=WHITE, insertcolor=GREEN,
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE)
        s.map("TEntry", bordercolor=[("focus", GREEN)])
        s.configure("TButton", background=PANEL, foreground=GREEN, font=MONO_B, bordercolor=GREEN,
                    lightcolor=PANEL, darkcolor=PANEL, padding=(12, 4))
        s.map("TButton", background=[("active", "#0d2a0d"), ("disabled", BG)],
              foreground=[("active", CYAN), ("disabled", DIM)], bordercolor=[("disabled", EDGE)])
        s.configure("Go.TButton", foreground=BG, background=GREEN, bordercolor=GREEN, lightcolor=GREEN,
                    darkcolor=GREEN, font=("Consolas", 11, "bold"), padding=(18, 6))
        s.map("Go.TButton", background=[("active", CYAN), ("disabled", EDGE)], foreground=[("disabled", BG)])
        s.configure("TCheckbutton", background=PANEL, foreground=GREEN, font=MONO_B, indicatorcolor="#000000",
                    indicatorbackground="#000000")
        s.map("TCheckbutton", indicatorcolor=[("selected", GREEN)], background=[("active", PANEL)],
              foreground=[("active", CYAN)])
        s.configure("Scene.Horizontal.TProgressbar", background=MAGENTA, troughcolor="#000000",
                    bordercolor=EDGE, lightcolor=MAGENTA, darkcolor=MAGENTA)

    def _build(self):
        f = ttk.Frame(self, padding=(16, 12))
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        ban = tk.Text(f, height=len(BANNER), width=len(BANNER[0]), bg=BG, fg=GREEN, bd=0, highlightthickness=0,
                      font=("Consolas", 9, "bold"), cursor="arrow")
        for i, (line, col) in enumerate(zip(BANNER, BANNER_COLOURS)):
            ban.tag_configure(f"l{i}", foreground=col)
            ban.insert("end", line + ("\n" if i < len(BANNER) - 1 else ""), f"l{i}")
        ban.configure(state="disabled")
        ban.grid(row=0, column=0, columnspan=2, sticky="w")
        art = HackerArt(f, bg=BG)
        art.grid(row=0, column=3, rowspan=5, sticky="ne", padx=(16, 0))
        ttk.Label(f, text=f"  -=[ RBF -> SOURCE ]=-   {VERSION}   ::   cyclone v {convert.btir.MODEL}   ::   "
                          "mister de10-nano", foreground=MAGENTA).grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="─" * 78, style="Dim.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))

        pad = {"padx": (0, 8), "pady": 4}
        ttk.Label(f, text="[ INPUT  ]", foreground=CYAN).grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.rbf, font=MONO).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="BROWSE", command=self._pick_rbf).grid(row=3, column=2, padx=(8, 0), pady=4)
        ttk.Label(f, text="[ OUTPUT ]", foreground=CYAN).grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.out, font=MONO).grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="BROWSE", command=self._pick_out).grid(row=4, column=2, padx=(8, 0), pady=4)

        box = ttk.Frame(f, style="Panel.TFrame", padding=(12, 8))
        box.grid(row=5, column=0, columnspan=4, sticky="ew", pady=10)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="┌─[ PAYLOAD ]", style="PanelDim.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        for r, (key, _, name, desc) in enumerate(OPTIONS, start=1):
            tk.Checkbutton(box, text=f" {name}", variable=self.opt[key], bg=PANEL, fg=GREEN, selectcolor="#000000",
                           activebackground=PANEL, activeforeground=CYAN, font=MONO_B, bd=0, highlightthickness=0,
                           anchor="w").grid(row=r, column=0, sticky="w", pady=1)
            ttk.Label(box, text=desc, style="PanelDim.TLabel").grid(row=r, column=1, sticky="w", padx=(16, 0))

        bar = ttk.Frame(f)
        bar.grid(row=6, column=0, columnspan=4, sticky="ew")
        self.run_btn = ttk.Button(bar, text=">> CONVERT <<", style="Go.TButton", command=self._run)
        self.run_btn.pack(side="left")
        self.open_btn = ttk.Button(bar, text="OPEN OUTPUT", command=self._open_out, state="disabled")
        self.open_btn.pack(side="left", padx=10)
        self.rb_btn = ttk.Button(bar, text="REBUILD RBF", command=self._rebuild)
        self.rb_btn.pack(side="left")
        self.prog = ttk.Progressbar(bar, mode="indeterminate", length=200, style="Scene.Horizontal.TProgressbar")
        self.prog.pack(side="right")
        self.status = ttk.Label(f, text="[*] ready. load an rbf.", style="Status.TLabel")
        self.status.grid(row=7, column=0, columnspan=4, sticky="w", pady=(10, 4))

        self.log = tk.Text(f, height=12, wrap="word", state="disabled", font=("Consolas", 9), bg="#000000", fg=GREEN,
                           insertbackground=GREEN, bd=0, highlightthickness=1, highlightbackground=EDGE,
                           highlightcolor=EDGE, padx=8, pady=6)
        self.log.tag_configure("ok", foreground=CYAN)
        self.log.tag_configure("bad", foreground=MAGENTA)
        self.log.grid(row=8, column=0, columnspan=4, sticky="nsew")
        f.rowconfigure(8, weight=1)
        ttk.Label(f, text=" All your source are belong to us", style="Dim.TLabel"
                  ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(f, text="Created by Meathax ", foreground=MAGENTA
                  ).grid(row=9, column=0, columnspan=4, sticky="e", pady=(6, 0))

    def _pick_rbf(self):
        ps = filedialog.askopenfilenames(title="Select RBF(s) - Ctrl/Shift-click for several",
                                         filetypes=[("FPGA bitstream", "*.rbf"), ("All files", "*.*")])
        if ps:
            self.rbf.set("; ".join(ps))
            if not self.out.get():
                p = Path(ps[0])
                self.out.set(str(p.with_name(p.stem + "_source") if len(ps) == 1 else p.parent / "mistery_source"))

    def _inputs(self):
        """Entry text -> RBF paths: several separated by ';', or a folder (all its .rbf files)."""
        return convert.collect([s.strip().strip('"') for s in self.rbf.get().split(";") if s.strip()])

    def _pick_out(self):
        p = filedialog.askdirectory(title="Select output folder", mustexist=False)
        if p:
            self.out.set(p)

    def _open_out(self):
        p = self.out.get()
        if p and Path(p).is_dir():
            os.startfile(p) if sys.platform == "win32" else subprocess.Popen(["xdg-open", p])

    def _write(self, line):
        tag = "ok" if "byte-identical" in line or line.startswith("Done") else \
              "bad" if ("WARNING" in line or "Traceback" in line or "Error" in line) else None
        self.log.configure(state="normal")
        self.log.insert("end", "> " + line + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _run(self):
        rbfs, out = self._inputs(), self.out.get().strip()
        if not rbfs:
            messagebox.showerror(TITLE, "Select one or more RBF files (or a folder containing them).")
            return
        if not out:
            messagebox.showerror(TITLE, "Select an output folder.")
            return
        o = {k: v.get() for k, v in self.opt.items()}
        if not any(o.values()):
            messagebox.showerror(TITLE, "Select at least one output.")
            return
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.status.configure(text=f"[>] working on {len(rbfs)} rbf{'s' if len(rbfs) > 1 else ''} ...", foreground=CYAN)
        self.prog.start(12)
        self.worker = threading.Thread(target=self._work, args=(rbfs, out, o), daemon=True)
        self.worker.start()

    def _rebuild(self):
        src = filedialog.askopenfilename(title="Select edited source (NAME.v, sidecar next to it)",
                                         filetypes=[("Rebuildable source", "*.v"), ("All files", "*.*")])
        if not src:
            return
        if src.endswith(("_rtl.v", "_sim.v")):
            messagebox.showerror(TITLE, "Pick NAME.v (the rebuildable source with NAME.sidecar.json), not _rtl.v/_sim.v.")
            return
        dst = filedialog.asksaveasfilename(title="Save rebuilt RBF as", defaultextension=".rbf",
                                           initialfile=Path(src).stem + "_mod.rbf", filetypes=[("RBF", "*.rbf")])
        if not dst:
            return
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
        for b in (self.run_btn, self.rb_btn):
            b.configure(state="disabled")
        self.status.configure(text="[>] rebuilding rbf ...", foreground=CYAN)
        self.prog.start(12)

        def work():
            try:
                res = convert.rebuild(src, dst, log=lambda s: self.msgs.put(("log", s)))
                self.msgs.put(("rebuilt", res))
            except Exception as e:
                self.msgs.put(("log", traceback.format_exc().rstrip()))
                self.msgs.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _work(self, rbfs, out, o):
        try:
            t0 = time.time()
            results = convert.convert_many(rbfs, out, source=o["source"], sim=o["sim"], verify=o["verify"],
                                           ir_json=o["ir"], rtl=o["rtl"], log=lambda s: self.msgs.put(("log", s)))
            if len(results) == 1:
                rbf, res, err = results[0]
                self.msgs.put(("done", res) if res else ("error", err))
            else:
                self.msgs.put(("batch", (results, round(time.time() - t0, 1))))
        except Exception as e:  # shown to the user, details in the log
            self.msgs.put(("log", traceback.format_exc().rstrip()))
            self.msgs.put(("error", str(e)))

    def _pump(self):
        try:
            while True:
                kind, v = self.msgs.get_nowait()
                if kind == "log":
                    self._write(v)
                else:
                    self.prog.stop()
                    self.run_btn.configure(state="normal")
                    self.rb_btn.configure(state="normal")
                    if kind == "rebuilt":
                        self.status.configure(text=f"[+] rbf rebuilt in {v['seconds']} s :: {Path(v['rbf']).name}",
                                              foreground=GREEN)
                        self.out.set(str(Path(v["rbf"]).parent))
                        self.open_btn.configure(state="normal")
                    elif kind == "batch":
                        results, secs = v
                        ok = sum(1 for _, r, _ in results if r and r["verified"] in (None, True))
                        self.status.configure(text=f"[+] batch: {ok}/{len(results)} converted in {secs} s",
                                              foreground=GREEN if ok == len(results) else MAGENTA)
                        self.open_btn.configure(state="normal")
                    elif kind == "done":
                        ok = {None: "", True: " :: rebuild verified byte-identical",
                              False: " :: WARNING rebuild differs"}[v["verified"]]
                        self.status.configure(text=f"[+] done in {v['seconds']} s{ok}",
                                              foreground=MAGENTA if v["verified"] is False else GREEN)
                        self.open_btn.configure(state="normal")
                    else:
                        self.status.configure(text="[!] failed.", foreground=MAGENTA)
                        messagebox.showerror(TITLE, v)
        except queue.Empty:
            pass
        self.after(100, self._pump)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:  # crisp text on scaled displays; own taskbar icon instead of python's
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mistery.rbf2src")
        except Exception:
            pass
    App().mainloop()
