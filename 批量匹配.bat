@echo off
chcp 65001 >/dev/null
cd /d C:\Users\Administrator\Documents\trae_projects\auto-quota
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python tools\batch_loop.py >> output\batch\batch_loop.log 2>&1
