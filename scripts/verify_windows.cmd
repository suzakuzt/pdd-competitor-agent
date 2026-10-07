@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONUTF8=1"
pushd "%~dp0.."
if errorlevel 1 exit /b 6
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 -m pdd_monitor --data-dir data validate
set "VERIFY_RESULT=%ERRORLEVEL%"
goto finish

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python -m pdd_monitor --data-dir data validate
set "VERIFY_RESULT=%ERRORLEVEL%"
goto finish

:no_python
echo ERROR: Python 3.10 or newer was not found via py -3 or python.
set "VERIFY_RESULT=2"

:finish
popd
if not "%VERIFY_RESULT%"=="0" echo Validation failed. Exit code: %VERIFY_RESULT%
exit /b %VERIFY_RESULT%
