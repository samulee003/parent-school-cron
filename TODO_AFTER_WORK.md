# 回來後的待辦清單

> 只需要 3 件事，10 分鐘搞定

---

## ✅ 已完成（我幫你做好的）

- [x] 代碼推送到 GitHub：https://github.com/samulee003/parent-school-cron
- [x] Zeabur 部署成功（Tencent Tokyo 伺服器）
- [x] 域名綁定：`parent-school.zeabur.app`（正在生效中）
- [x] 環境變量已設置：`WXAGENT_PUSH_DAY=mon`、`HOUR=9`、`MINUTE=0`、`CRON_SECRET=（已生成隨機密鑰）`
- [x] 修掉了代碼裡的硬編碼 URL
- [x] 設定指南已寫好：`SETUP_GUIDE.md`

---

## 🔲 你回來要做的

### 第 1 件：註冊企業微信 + 拿 Webhook URL（5 分鐘）

1. 打開 https://work.weixin.qq.com/ → 立即註冊 → 選「團隊」
2. 手機裝企業微信 App
3. 建群 → 群機器人 → 添加機器人 → 複製 Webhook URL
4. 關閉群成員發言（讓群安靜）

詳細步驟看 `SETUP_GUIDE.md`

### 第 2 件：更新 Zeabur 的 Webhook URL（1 分鐘）

拿到 Webhook URL 後，告訴我，或者自己執行：

```bash
npx zeabur@latest variable update --id 6a0c2ec3bc2975dc87200b9c -k "WECOM_WEBHOOK_URL=你複製的URL" -y -i=false
```

### 第 3 件：在 Zeabur Dashboard 設置 Cron Job（2 分鐘）

CLI 沒有 cron 命令，需要手動設：

1. 打開 https://zeabur.com/projects/6a0c2eb7bc2975dc87200b96
2. 點進你的 Service
3. 找到「Cron Jobs」→「Add Cron Job」
4. 填寫：
   - **Schedule**: `0 1 * * 1`（UTC 時間 = 澳門時間週一 9:00）
   - **Command**: `curl -s "https://parent-school.zeabur.app/api/cron?secret=5YBPFA1VGRPENRbIclEJoW3YpDzHSdqCw6Og4QaebMY"`
5. 保存

---

## 🧪 設好後測試

打開瀏覽器訪問：
```
https://parent-school.zeabur.app/health
```
如果看到 JSON 回應，說明服務正常。

手動觸發推送測試：
```
https://parent-school.zeabur.app/api/cron?secret=5YBPFA1VGRPENRbIclEJoW3YpDzHSdqCw6Og4QaebMY
```

---

## 重要資訊

| 項目 | 值 |
|------|-----|
| GitHub | https://github.com/samulee003/parent-school-cron |
| Zeabur Dashboard | https://zeabur.com/projects/6a0c2eb7bc2975dc87200b96 |
| 服務域名 | https://parent-school.zeabur.app |
| Service ID | 6a0c2ec3bc2975dc87200b9c |
| CRON_SECRET | 5YBPFA1VGRPENRbIclEJoW3YpDzHSdqCw6Og4QaebMY |
