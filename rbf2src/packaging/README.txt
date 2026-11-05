Mistery
=======

Turns a MiSTer Cyclone V core (RBF for the DE10-Nano, device 5CSEBA6U23I7) into source.

Setup
  Unzip anywhere and double-click Mistery.exe. Windows 10 or 11, 64-bit. Nothing to install: no Python,
  no runtime packages. Keep this folder together: the exes need _internal\ and mistral-cv\ next to them.

Using it
  GUI: pick one or more RBFs (Ctrl/Shift-click, or type a folder), pick an output folder, tick what to
       produce, press Convert. Several RBFs each get their own subfolder.
  Rebuild: press REBUILD RBF, pick your edited NAME.v (NAME.sidecar.json next to it), save the new RBF.
  Command line: mistery-convert.exe NAME.rbf [MORE.rbf ... | FOLDER] OUTDIR [--no-rtl] [--no-source] [--no-sim] [--no-verify] [--ir]
                mistery-convert.exe --rebuild NAME.v NEW.rbf

Output for NAME.rbf
  NAME_rtl.v                  RTL Verilog: synthesisable, self-contained HDL. LUTs are boolean logic, flip-flops
                              always blocks, RAMs arrays with their contents, DSPs multiplies. It is flattened
                              gate-level logic named after the hardware, not the original module structure.
  NAME.v, NAME.sidecar.json   rebuildable source: one instance per configured site, settings as parameters,
                              routing in the sidecar. Unchanged, it rebuilds the RBF byte for byte.
  NAME_sim.v, rbf_prims.v     behavioural simulation model
  NAME.rebuilt.rbf            the RBF rebuilt from the source (Verify); the log says whether it is byte-identical
  NAME.ir.json                decoded netlist (optional, large)
  NAME.summary.txt            input hash, files written, verification result

Blocks not modelled yet (PLLs, the HPS/DDR interface, some RAM/DSP modes) are listed in the summary and left
undriven in NAME_rtl.v and NAME_sim.v. Simulating the Verilog needs a simulator (for example Icarus Verilog);
the app itself does not.
