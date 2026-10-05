@echo off
rem ChordLayer Studio - double-click launcher (FL Studio 20 MIDI round-trip).
rem Uses -B so Python writes no __pycache__ folders, and installs nothing.

setlocal
set "APP=%~dp0app\chordlayer_app.py"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -B "%APP%" %*
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -B "%APP%" %*
    goto :eof
)

echo Python was not found on PATH.
echo ChordLayer needs an existing Python 3 install; it does not install anything itself.
echo If you have Python, run:  python -B "%~dp0app\chordlayer_app.py"
pause
