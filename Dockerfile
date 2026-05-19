# Zeabur 適配 Dockerfile
# Zeabur 會自動使用此文件構建容器

FROM python:3.10-slim

WORKDIR /app

# 安裝系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 複製依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製源碼
COPY src/ ./src/
COPY zeabur.yaml .

# 創建數據目錄
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# 環境變量
ENV PYTHONUNBUFFERED=1
ENV WXAGENT_DATA_DIR=/app/data

# Zeabur 會自動設置 PORT 環境變量
EXPOSE 8000

# 啟動命令
CMD python src/api_server.py
