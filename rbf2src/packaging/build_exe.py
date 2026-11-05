#!/usr/bin/env python3
"""Build the Mistery app (RBF -> source) as Windows executables.

Output: rbf2src/dist/Mistery/
  Mistery.exe            GUI (double-click)
  mistery-convert.exe    command line: mistery-convert NAME.rbf OUTDIR [--no-source] [--no-sim] [--no-verify] [--ir]
  mistral-cv/            mistral-cv.exe + MinGW UCRT64 runtime DLLs (its device database is built in)
  _internal/             Python runtime and bundled resources
Needs: Python 3 with PyInstaller (py -3.14 -m pip install --user pyinstaller), build-win/mistral-cv/mistral-cv.exe
(see README Build), MSYS2 UCRT64 runtime DLLs (default C:/msys64/ucrt64/bin, override with --dlls DIR).
Release folder: rbf2src/release/Mistery/ = the app + README.txt, self-contained (the bundle carries its own
VC++/UCRT runtime DLLs). --with-redist adds the Microsoft VC++ x64 redistributable and an install script.
mistral-cv: build-slim/ (MiSTer-only database, see libmistral/CMakeLists.txt) if built, else build-win/.
Usage: py -3.14 rbf2src/packaging/build_exe.py [--dlls DIR] [--mcv FILE] [--with-redist [--redist FILE]] [--no-release]
"""
import shutil, subprocess, sys
from pathlib import Path

REDIST_CANDIDATES = sorted(Path("C:/Program Files/Microsoft Visual Studio").glob("*/*/VC/Redist/MSVC/14.*/vc_redist.x64.exe"),
                           reverse=True) + sorted(Path("C:/Program Files (x86)/Microsoft Visual Studio").glob(
                               "*/*/VC/Redist/MSVC/14.*/vc_redist.x64.exe"), reverse=True)


def release(app, redist=None):
    """Assemble the end-user folder: app files, the VC++ redistributable and an install script."""
    rel = app.parent.parent / "release" / "Mistery"
    shutil.rmtree(rel, ignore_errors=True)
    shutil.copytree(app, rel)
    if redist:
        (rel / "prerequisites").mkdir()
        shutil.copy2(redist, rel / "prerequisites" / "vc_redist.x64.exe")
        shutil.copy2(HERE / "Install-prerequisite.bat", rel / "1 - Install prerequisite.bat")
    shutil.copy2(HERE / "README.txt", rel / "README.txt")
    return rel

HERE = Path(__file__).resolve().parent
R = HERE.parent
DLLS = ["libgcc_s_seh-1.dll", "liblzma-5.dll", "libstdc++-6.dll", "libwinpthread-1.dll"]


def main(argv):
    dlldir = Path(argv[argv.index("--dlls") + 1]) if "--dlls" in argv else Path("C:/msys64/ucrt64/bin")
    slim = R.parent / "build-slim" / "mistral-cv" / "mistral-cv.exe"
    mcv = Path(argv[argv.index("--mcv") + 1]) if "--mcv" in argv else (
        slim if slim.exists() else R.parent / "build-win" / "mistral-cv" / "mistral-cv.exe")
    print("mistral-cv:", mcv)
    for need in [mcv, R / "build" / "tables" / "p2p.txt"] + [dlldir / d for d in DLLS]:
        if not need.exists():
            sys.exit(f"missing: {need}")
    work, dist = R / "build" / "pyinstaller", R / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--workpath", str(work),
                    "--distpath", str(dist), str(HERE / "Mistery.spec")], check=True)
    app = dist / "Mistery"
    (app / "mistral-cv").mkdir(exist_ok=True)
    shutil.copy2(mcv, app / "mistral-cv" / mcv.name)
    for d in DLLS:
        shutil.copy2(dlldir / d, app / "mistral-cv" / d)
    shutil.copy2(HERE / "README.txt", app / "README.txt")
    shutil.rmtree(work, ignore_errors=True)
    size = sum(f.stat().st_size for f in app.rglob("*") if f.is_file())
    print(f"built {app} ({size / 1e6:.0f} MB)")
    if "--no-release" not in argv:
        redist = None
        if "--with-redist" in argv:
            redist = Path(argv[argv.index("--redist") + 1]) if "--redist" in argv else next(iter(REDIST_CANDIDATES), None)
            if redist is None or not redist.exists():
                sys.exit("no vc_redist.x64.exe found: pass --redist FILE (Microsoft Visual C++ 2015-2022 x64 redistributable)")
        rel = release(app, redist)
        size = sum(f.stat().st_size for f in rel.rglob("*") if f.is_file())
        print(f"release {rel} ({size / 1e6:.0f} MB{', prerequisite from ' + str(redist) if redist else ''})")


if __name__ == "__main__":
    main(sys.argv[1:])
