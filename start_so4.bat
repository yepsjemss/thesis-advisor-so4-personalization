@echo off
setlocal
pushd "%~dp0"
if not exist ".venv-so4\Scripts\python.exe" (
    echo Run configure_so4.bat once first.
    pause
    popd
    exit /b 1
)
if not exist ".env.personalization" (
    echo Run configure_so4.bat once first to set up the separate local MySQL database.
    pause
    popd
    exit /b 1
)
".venv-so4\Scripts\python.exe" run_personalization.py
pause
popd
