# Kino Bot — to'liq paket (2GB videolar uchun)

## Nima tuzatildi
1. **API_ID xatosi** (37366974 → 3736974) tuzatildi
2. **Pyrogram (MTProto)** qo'shildi — Cloud Bot API 20MB cheklovini chetlab o'tib **2GB gacha** video yuklab oladi
3. **Watermark** har qanday hajmdagi videoga ishlaydi
4. **Caption** dan ortiqcha "ID / @username / ogohlantirish" matnlari olib tashlandi — faqat video ichida suzib o'tadi
5. **`telegram-bot-api` supervisor blokini ishlatmaymiz** — Pyrogram tufayli kerak emas, FATAL xatolik chiqmaydi

## Railway'da deploy

1. Bu papkani GitHub repo'ga push qiling
2. Railway → New Project → Deploy from GitHub repo
3. **Variables** bo'limiga `.env.example`'dan ko'chiring (BOT_TOKEN, DATABASE_URL ni o'zingiznikiga almashtiring)
4. **Postgres plugin** qo'shing — `DATABASE_URL` avtomatik to'lib qoladi
5. Deploy

## Tekshirish
Birinchi ishga tushganda logda ko'rinishi kerak:
```
✅ Pyrogram (MTProto) ulandi — 2GB gacha fayl yuklab olish mumkin
🚀 RAM cache: N kino, M user yuklandi
```

Katta video kelganda:
```
bot.get_file xato (File is too big) — Pyrogram MTProto ishlatiladi
✅ Pyrogram MTProto orqali yuklandi: 450 MB → /tmp/wm_in_xxx.mp4
Watermark: AwACAg... yuklab olindi (450 MB)
```

## Fayllar
- `bot.py` — asosiy bot (Pyrogram fallback bilan)
- `requirements.txt` — Python kutubxonalar (pyrogram + tgcrypto qo'shildi)
- `Dockerfile` — Railway uchun ffmpeg bilan image
- `.env.example` — environment variables namunasi
- `railway.toml` — Railway konfiguratsiyasi
- `Procfile` — agar Heroku/Render ishlatsangiz

## Local API kerak emas
Eski `supervisord.conf` ichidagi `[program:telegram-bot-api]` blok endi kerak emas — Pyrogram MTProto Telegram serverlariga to'g'ridan-to'g'ri ulanadi va 2GB gacha yuklab oladi. Agar supervisord ishlatayotgan bo'lsangiz, o'sha blokni o'chiring.
