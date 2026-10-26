# وضعیت واردکننده فیلم Masiha

بررسی 2026-10-26: صفحه‌های فیلم، نقشه سایت و WordPress API سایت HexDL به سرور پاسخ Bot Verification می‌دهند. مسیر /mag/ خبری است و فایل دانلود فیلم ارائه نمی‌کند. بدون دسترسی مجاز مستقیم و روش خواندن واقعی صفحات نمی‌توان کاتالوگ یا لینک‌های ویدیو را با اطمینان استخراج کرد.

برنامه آماده: رکورد مستقل فیلم و سریال، فصل/قسمت، کیفیت‌های 480p/720p/1080p، دوبله و زیرنویس هر نسخه، امتیاز و منبع، کاور، صف دانلود/آپلود و حذف فایل موقت پس از ثبت تلگرام.

الزام Telegram Local Bot API: نیاز به api_id و api_hash حساب تلگرام در my.telegram.org/apps؛ فقط در فایل امن سرور ذخیره شود. Local Bot API رسمی حدود ۲۰۰۰MB آپلود را پشتیبانی می‌کند. تا زمان راه‌اندازی، local_bot_api:false است و worker نباید آپلود کند.

پیکربندی: /etc/masiha-vpn-extension/sales/film_import.json

نمونه JSONL/JSON با آرایه‌ای از اشیای دارای مجوز، source=hexdl,external_key,rights_ok=true,media_type=movie|series,title,year,director,actors,genre,description,rating,rating_source,cover_url,variants=[{quality:720p,url:https://approved-host.example/file.mp4,subtitles:softsub,dubbed:no,language:English}]. مثال میزبان و فایل واقعی نیست. download_hosts در config باید فهرست دقیق میزبان‌های معتبر واقعی را داشته باشد.

دستورات: sudo python3 /opt/masiha-vpn-extension/film_importer.py status ؛ sudo python3 /opt/masiha-vpn-extension/film_importer.py import-manifest /path/to/manifest.json ؛ sudo systemctl start masiha-film-import.service.

اعلان broadcast_movie و broadcast_series پس از درج فایل اول هر فیلم یا قسمت سریال در outbox یک‌بار به کاربران فعال ارسال می‌شود؛ برای هر کیفیت اعلان تکراری ایجاد نمی‌شود. نمایش صفحه ابتدا مشخصات و کاور، سپس انتخاب کیفیت است. موزیک‌ها دست‌نخورده‌اند. آخرین وضعیت واقعی دانلود از HexDL: هیچ فیلمی هنوز وارد صف و آپلود نشده است، چون دسترسی به لینک واقعی بسته است.