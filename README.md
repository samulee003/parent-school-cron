# 📚 家長學堂課程推送 Agent v2

> **家長零操作** — 掃碼加好友 → AI對話設定 → 自動每週推送

---

## 家長使用流程（超簡單）

```
📱 家長掃碼加機器人好友
    ↓
🤖 機器人發送歡迎消息 + 年齡選擇
    ↓
👆 家長回覆「1」或「1,3」（選年齡層）
    ↓
✅ 機器人確認設定
    ↓
📬 每週一早上 9 點自動收到課程推送
```

**就這樣！之後什麼都不用做。**

---

## 管理員部署（一次設定）

### 方式一：一鍵啟動（最簡單）

#### macOS / Linux
```bash
git clone <倉庫地址>
cd parent-academy-agent
chmod +x start.sh
./start.sh
```

#### Windows
```bash
git clone <倉庫地址>
cd parent-academy-agent
start.bat
```

第一次啟動會顯示 **QR Code 網址**，用手機微信掃碼登錄機器人即可。

### 方式二：Docker（推薦長期運行）

```bash
docker compose up -d
```

### 方式三：手動安裝

```bash
# 1. 安裝 Python 3.10+
# 2. 安裝依賴
pip install -r requirements.txt

# 3. 啟動
python src/main.py
```

---

## 配置選項

| 環境變量 | 說明 | 默認 |
|----------|------|------|
| `WXAGENT_PUSH_DAY` | 推送星期 (mon-sun) | mon |
| `WXAGENT_PUSH_HOUR` | 推送小時 (0-23) | 9 |
| `WXAGENT_PUSH_MINUTE` | 推送分鐘 (0-59) | 0 |
| `WXAGENT_DATA_DIR` | 數據目錄 | ./data |
| `WXAGENT_LOG_LEVEL` | 日誌級別 | INFO |
| `WECHATY_PUPPET` | Wechaty 協議 | wechaty-puppet-wechat |

---

## 家長能說什麼

| 回覆 | 功能 |
|------|------|
| `1`, `2`, `3`, `4` | 選擇年齡層 |
| `1,3` | 同時選多個年齡層 |
| `完成` | 確認選擇 |
| `確認` | 完成設定 |
| `修改` | 重新設定年齡層 |
| `停止` | 暫停接收推送 |
| `開始` | 恢復接收推送 |
| `狀態` | 查看當前設定 |
| `幫助` | 查看使用說明 |

---

## 年齡層分類

| 數字 | 年齡層 | 階段 |
|------|--------|------|
| 1 | 0-2歲 | 嬰幼兒期 |
| 2 | 3-6歲 | 幼兒期 |
| 3 | 7-12歲 | 兒童期 |
| 4 | 13-18歲 | 青少年期 |

---

## 推送消息預覽

```
📚 家長學堂 — 本週精選課程

為您精選了 5 個活動：

**1. 公共圖書館—嬰幼繪本氹氹轉—《可愛的你》**
📅 2026/06/20 星期六 15:00-16:00
🏷️ 👶 嬰幼兒期 | 家庭關係 | 親子
🟢 報名中
[查看詳情]

**2. 家長學堂─幼兒的感官與大肌肉發展**
📅 2026/06/27 星期六 10:30-12:00
🏷️ 👶 嬰幼兒期 | 能力發展 | 親子
🟢 報名中
[查看詳情]

---
💡 回覆「修改」可調整年齡設定
📖 回覆「幫助」查看使用說明
```

---

## 項目結構

```
parent-academy-agent/
├── start.sh                  # 一鍵啟動 (Linux/Mac)
├── start.bat                 # 一鍵啟動 (Windows)
├── Dockerfile                # Docker 構建
├── docker-compose.yml        # Docker Compose
├── requirements.txt          # Python 依賴
├── README.md                 # 本文檔
├── data/                     # 數據目錄 (自動創建)
│   ├── users.db             # SQLite 用戶數據庫
│   └── agent.log            # 運行日誌
└── src/
    ├── main.py              # 主入口
    ├── bot_server.py        # 微信機器人服務
    ├── chat_flow.py         # AI 對話流程
    ├── user_store.py        # SQLite 用戶存儲
    ├── scraper.py           # 課程爬蟲
    ├── classifier.py        # 課程分類器
    ├── config.py            # 配置管理
    ├── subscription.py      # 訂閱管理 (v1兼容)
    ├── wechat_bot.py        # 企業微信推送 (備用)
    └── scheduler.py         # 定時排程 (備用)
```

---

## 技術架構

```
家長(微信) ←→ Wechaty 機器人 ←→ 對話流程引擎 ←→ 用戶數據庫
                                           ↓
                                    APScheduler 定時任務
                                           ↓
                                    課程爬蟲 ←→ 家長學堂 API
                                           ↓
                                    按年齡層分類推送
                                           ↓
                                    微信私聊推送
```

---

## 數據來源

- **網站**: 澳門教育及青年發展局 — 家長學堂
- **API**: https://portal.dsedj.gov.mo/webdsejspace/site/parent_academy/course.jsp

---

## 注意事項

1. **Wechaty 協議選擇**：
   - `wechaty-puppet-wechat`（免費，網頁版，可能不穩定）
   - `wechaty-puppet-padlocal`（推薦，需 token，更穩定）

2. **掃碼登錄**：每次重啟需要重新掃碼登錄

3. **數據存儲**：用戶數據保存在 `data/users.db`（SQLite）

4. **日誌**：運行日誌在 `data/agent.log`

---

## 開發

```bash
# 運行測試
python -m pytest tests/

# 代碼格式化
python -m black src/
```

## License

MIT License
