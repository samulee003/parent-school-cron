# Project Notes

- Current product direction: WhatsApp-first MVP.
- Main service entry: `src/api_server.py`.
- Deploy target: Zeabur or any container host exposing `PORT` (default `8000`).
- Do not commit real tokens, secrets, service IDs, phone IDs, or webhook URLs.
- Legacy WeCom modules remain in `src/` for reference, but new work should prefer WhatsApp unless the user explicitly asks otherwise.

## Required Runtime Secrets

Use environment variables only:

- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_APP_SECRET`
- `CRON_SECRET`
- `ADMIN_SECRET`
