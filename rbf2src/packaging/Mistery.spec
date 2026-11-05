# PyInstaller spec: windowed GUI (Mistery.exe) + console CLI (mistery-convert.exe) in one folder.
# Build with packaging/build_exe.py (it also copies mistral-cv.exe and its DLLs next to the exes).
from pathlib import Path
R = Path(SPECPATH).parent                       # rbf2src/
paths = [str(R), str(R / "netlist")]
datas = [(str(R / "sim" / "rbf_prims.v"), "sim"), (str(R / "build" / "tables" / "p2p.txt"), "tables"),
         (str(R / "packaging" / "mistery.ico"), "."), (str(R / "packaging" / "mistery.png"), ".")]
ICON = str(R / "packaging" / "mistery.ico")
hidden = ["btir", "sitev", "simv", "alm"]
gui = Analysis([str(R / "gui.py")], pathex=paths, datas=datas, hiddenimports=hidden,
               excludes=["numpy"])
cli = Analysis([str(R / "convert.py")], pathex=paths, datas=datas, hiddenimports=hidden,
               excludes=["numpy", "tkinter"])
MERGE((gui, "Mistery", "Mistery"), (cli, "mistery-convert", "mistery-convert"))
gexe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="Mistery", console=False, icon=ICON)
cexe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="mistery-convert", console=True, icon=ICON)
COLLECT(gexe, gui.binaries, gui.datas, cexe, cli.binaries, cli.datas, name="Mistery")
