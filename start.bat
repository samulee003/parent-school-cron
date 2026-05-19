@echo off
chcp 65001 >nul
:: 一鍵啟動腳本 (Windows)

echo ==========================================
echo   📚 家長學堂課程推送 Agent
echo ==========================================
echo.

:: 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 Python，請先安裝 Python 3.10+
    echo    https://python.org/downloads
    pause
    exit /b 1
)

echo ✅ Python:
python --version

:: 檢查虛擬環境
if not exist "venv" (
    echo 📦 創建虛擬環境...
    python -m venv venv
)

:: 激活虛擬環境
echo 🔌 激活虛擬環境...
call venv\Scripts\activate.bat

:: 安裝依賴
echo 📦 安裝依賴...
pip install -q -r requirements.txt

:: 創建數據目錄
if not exist "data" mkdir data

:: 啟動
echo.
echo 🚀 啟動機器人...
echo.
python src\main.py %*

pause
