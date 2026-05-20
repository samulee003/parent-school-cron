# 下一步待辦

## 已整理

- [x] 專案主線改為 WhatsApp-first。
- [x] WhatsApp「課程」查詢改用實際 `CourseScraper.fetch_all_open_courses()`。
- [x] 支援年齡層查詢：`0-2歲`、`3-6歲`、`7-12歲`、`13-18歲`。
- [x] WhatsApp webhook 支援 Meta `X-Hub-Signature-256` 驗證。
- [x] `/api/cron`、`/api/push`、`/api/users` 加上密鑰保護。
- [x] 文件移除硬編碼 secret，改用環境變數佔位。

## 還需要你在平台上完成

- [ ] 確認 Meta WhatsApp App 的 `Phone Number ID`。
- [ ] 建立 Permanent Access Token。
- [ ] 設定 Webhook callback：`https://你的域名/api/whatsapp/webhook`。
- [ ] 訂閱 webhook field：`messages`。
- [ ] 在 Zeabur 填入 `.env.example` 裡的必填變數。
- [ ] 用白名單手機號傳「課程」做端到端測試。
- [ ] 要公開給所有家長前，換正式 WhatsApp 號碼並完成商業驗證。

## 注意

不要再把 token、secret、cron secret 寫進文件或提交到 Git。舊文件曾經含有敏感值，若這個 repo 已經推到 GitHub，請輪替那些密鑰。
