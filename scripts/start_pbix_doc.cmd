@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_offline.ps1" %*
set "pbix_doc_exit=%ERRORLEVEL%"
if not "%pbix_doc_exit%"=="0" (
    echo PBIX Converter failed with exit code %pbix_doc_exit%.
    pause
)
exit /b %pbix_doc_exit%
