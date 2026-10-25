@echo off
setlocal
pushd "%~dp0"
if not exist ".venv-so4\Scripts\python.exe" (
    py -3 -m venv .venv-so4
    if errorlevel 1 goto failed
)
".venv-so4\Scripts\python.exe" -m pip install -r requirements-so4.txt
if errorlevel 1 goto failed
".venv-so4\Scripts\python.exe" configure_so4.py
if errorlevel 1 goto failed
echo Setup complete. Run start_so4.bat.
pause
popd
exit /b 0
:failed
echo Setup did not finish. Read the message above.
pause
popd
exit /b 1
