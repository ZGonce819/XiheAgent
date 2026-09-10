@echo off
set VITE_DASHBOARD_API_BASE_URL=http://127.0.0.1:2024
cd /d "%~dp0..\ui"
node node_modules\vite\bin\vite.js dev --port 3000 --host 127.0.0.1
