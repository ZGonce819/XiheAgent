@echo off
cd /d D:\Bemyself\Python\xihe
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -m uvicorn app:app --host 127.0.0.1 --port 2024 --reload
