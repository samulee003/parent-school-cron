#!/bin/bash
# 一鍵啟動腳本 (Linux/Mac)

echo "=========================================="
echo "  📚 家長學堂課程推送 Agent"
echo "=========================================="
echo ""

# 檢查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，請先安裝 Python 3.10+"
    echo "   https://python.org/downloads"
    exit 1
fi

echo "✅ Python: $(python3 --version)"

# 檢查虛擬環境
if [ ! -d "venv" ]; then
    echo "📦 創建虛擬環境..."
    python3 -m venv venv
fi

# 激活虛擬環境
echo "🔌 激活虛擬環境..."
source venv/bin/activate

# 安裝依賴
echo "📦 安裝依賴..."
pip install -q -r requirements.txt

# 創建數據目錄
mkdir -p data

# 啟動
echo ""
echo "🚀 啟動機器人..."
echo ""
python src/main.py "$@"
