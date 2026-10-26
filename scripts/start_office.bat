@echo off
cd /d "%~dp0.."
py -3 -m violet_office.web serve --open-browser
pause
