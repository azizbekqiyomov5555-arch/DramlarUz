# Kino Bot — 2GB Upload (Local Telegram Bot API)

## Nima o'zgardi
- ✅ `API_ID` va `API_HASH` (my.telegram.org dan) qo'shildi
- ✅ Docker ichida **telegram-bot-api** server ishlaydi → **2 GB gacha** video
- ✅ `Application.builder()` lokal API'ga ulanadi (`local_mode=True`)
- ✅ Limit: `48 MB → 1950 MB`, watermark: `47 MB → 1900 MB`
- ✅ `supervisord` ikkala protsessni (API server + bot) boshqaradi

## Railway'ga deploy

1. Bu papkani **GitHub repo**'ga yuklang (`bot.py`, `Dockerfile`, `supervisord.conf`, `requirements.txt`, `railway.json`).
2. Railway → **New Project → Deploy from GitHub repo** → repo'ni tanlang.
3. **Variables** bo'limiga quyidagilarni qo'shing (`.env.example` dan ko'chiring):
   - `BOT_TOKEN`
   - `ADMIN_ID`
   - `DATABASE_URL` (Railway Postgres plugin'dan oling)
   - `API_ID=37366974`
   - `API_HASH=08d09c7ed8b7cb414ed6a99c104f1bd6`
   - `CHECKCARD_SHOP_ID`, `CHECKCARD_SHOP_KEY`
   - `RAILWAY_URL=https://<sizning-app>.up.railway.app/checkcard_webhook`
4. Deploy. Birinchi build **~5-8 daqiqa** (telegram-bot-api kompilatsiya bo'ladi).
5. Loglarda ko'rasiz:
   ```
   ✅ LOCAL Bot API ishlatilmoqda: http://127.0.0.1:8081/bot (limit ~2GB)
   ```

## Tekshirish
Botga 100-500 MB video yuboring → endi to'liq watermark (`⚠️ O'g'irlash taqiqlanadi!` + user ID + username) qo'shiladi va qaytariladi.

## Eslatma
- Railway $5 plan: RAM 512 MB / disk 1 GB. Juda katta (1.5 GB+) videolarda RAM yetmasligi mumkin — bunday hollarda **Hetzner / Contabo VPS** tavsiya etiladi.
- Lokal API server `/var/lib/telegram-bot-api` ga keshlaydi. Railway disk ephemeral — restartda tozalanadi (bu normal).
