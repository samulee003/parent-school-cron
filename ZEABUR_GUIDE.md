# Zeabur 部署指南

> 適配企業微信群機器人（Webhook 方案）— 零長連接，最穩定

---

## 架構

```
Zeabur (Docker Container)
├── FastAPI HTTP 服務器 (端口 8000)
├── Cron Job (每週一 9:00 觸發)
├── SQLite 用戶數據庫
└── 課程爬蟲 + 分類器

企業微信群
├── 群機器人 (Webhook URL)
└── 家長在群裡 @機器人 互動
```

**工作流程：**
1. 家長在群裡 @機器人 設定年齡層
2. Zeabur Cron 每週一自動抓取課程
3. 按訂閱分類後，Webhook 推送到企業微信群

---

## 部署步驟（5 分鐘完成）

### Step 1：創建企業微信群機器人

1. 打開企業微信，創建或進入一個群組
2. 點擊群右上角「...」→「群機器人」→「添加機器人」
3. 設置機器人名稱（如「家長學堂助手」）
4. **複製 Webhook URL**（格式：`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx`）

### Step 2：上傳代碼到 GitHub

```bash
git init
git add .
git commit -m "init"
git remote add origin https://github.com/yourname/parent-academy-bot.git
git push -u origin main
```

### Step 3：在 Zeabur 部署

1. 登入 [Zeabur Console](https://zeabur.com)
2. 點「Create Project」
3. 選「Deploy from GitHub」
4. 選擇你的倉庫
5. Zeabur 會自動識別 Dockerfile 並部署

### Step 4：設置環境變量

在 Zeabur Console → 你的 Service → Variables 頁面，添加以下變量：

| 變量名 | 值 | 說明 |
|--------|-----|------|
| `WECOM_WEBHOOK_URL` | `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx` | 群機器人 Webhook |
| `WXAGENT_PUSH_DAY` | `mon` | 推送星期 |
| `WXAGENT_PUSH_HOUR` | `9` | 推送小時（UTC+8） |
| `WXAGENT_PUSH_MINUTE` | `0` | 推送分鐘 |
| `CRON_SECRET` | `隨機密碼` | Cron 接口密鑰 |

### Step 5：設置 Cron Job（定時推送）

在 Zeabur Console → 你的 Service → Cron Jobs：

```
Schedule: 0 9 * * 1
Command: curl "https://你的域名.zeabur.app/api/cron?secret=你的CRON_SECRET"
```

> 你的域名可以在 Zeabur Console → Service → Domain 查看

---

## 家長使用方式

### 在群裡 @機器人 即可

| 家長輸入 | 效果 |
|---------|------|
| `@機器人 修改 0-2歲` | 訂閱 0-2歲 課程 |
| `@機器人 修改 0-2歲,3-6歲` | 訂閱多個年齡層 |
| `@機器人 狀態` | 查看自己的訂閱 |
| `@機器人 停止` | 暫停接收推送 |
| `@機器人 開始` | 恢復接收推送 |
| `@機器人 幫助` | 查看說明 |

### 每週自動推送

每週一早上 9 點，機器人會在群裡自動推送課程消息：

```
📚 家長學堂 — 本週精選課程

為您找到 12 個活動：

## 嬰幼兒期（0-2歲）— 3 個活動

• 公共圖書館—嬰幼繪本氹氹轉—《可愛的你》
  📅 2026/06/20 星期六 15:00-16:00
  🏷️ 家庭關係 | 親子
  🟢 報名中

---

💡 @機器人 修改 年齡 可調整訂閱
```

---

## API 接口

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/` | 健康檢查 |
| `GET` | `/health` | 服務狀態 |
| `POST` | `/api/push` | 手動觸發推送 |
| `POST` | `/api/cron?secret=xxx` | Cron 觸發（帶密鑰） |
| `POST` | `/api/webhook` | 接收企業微信回調 |
| `GET` | `/api/users` | 用戶列表 |

---

## 常見問題

### Q: 推送時間是 UTC+8 嗎？
A: 是，Zeabur Cron 默認 UTC，但本項目內部已做 UTC+8 轉換。設置 `WXAGENT_PUSH_HOUR=9` 就是澳門/北京時間早上 9 點。

### Q: 可以改推送時間嗎？
A: 可以，修改環境變量 `WXAGENT_PUSH_DAY` 和 `WXAGENT_PUSH_HOUR` 即可。

### Q: 數據會丟失嗎？
A: 不會，用戶數據存儲在 `/app/data/users.db`（SQLite），Zeabur Volume 會持久化。

### Q: 需要企業微信管理員嗎？
A: 不需要。任何人都可以創建群組並添加群機器人。

### Q: 免費額度夠用嗎？
A: Zeabur 免費額度：512MB RAM + 1GB 存儲，足夠本項目運行。

---

## 文件結構

```
.
├── Dockerfile              # Zeabur 自動構建
├── docker-compose.yml      # 本地測試
├── zeabur.yaml             # 環境變量模板
├── requirements.txt        # Python 依賴
├── ZEABUR_GUIDE.md         # 本文檔
├── src/
│   ├── api_server.py       # FastAPI HTTP 服務
│   ├── bot_webhook.py      # 核心 Bot 邏輯
│   ├── scraper.py          # 課程爬蟲
│   ├── classifier.py       # 課程分類
│   ├── user_store.py       # 用戶數據庫
│   └── config.py           # 配置管理
└── data/                   # 持久化數據（自動創建）
    └── users.db
```
