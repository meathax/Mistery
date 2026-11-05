@echo off
rem Double-click to open the Mistery GUI (needs Python 3 with tkinter; mistral-cv built in build-win).
start "" pyw -3.14 "%~dp0gui.py" || start "" pyw -3 "%~dp0gui.py"
