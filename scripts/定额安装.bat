@echo off
set TRANSFORMERS_OFFLINE=1
set HF_HUB_OFFLINE=1
set HF_DATASETS_OFFLINE=1
cd /d "%~dp0.."
python tools/quota_install_menu.py
pause
