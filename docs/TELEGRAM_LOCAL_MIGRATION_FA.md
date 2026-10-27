# انتقال ایمن ربات مسیحا به Local Telegram Bot API

- منبع رسمی: https://github.com/tdlib/telegram-bot-api
- فایل محرمانه شناسه API: /etc/default/telegram-bot-api با سطح دسترسی 0600؛ هرگز در Git ثبت نکنید.
- سرویس telegram-bot-api.service فقط روی 127.0.0.1:8081 گوش می‌دهد.
- فایل پرچم انتقال /etc/masiha-vpn-extension/sales/telegram_local.enabled باید فقط پس از تست سلامت سرویس محلی و اجرای logOut روی API ابری ایجاد شود.
- هرگز هم‌زمان روی هر دو API برای getUpdates درخواست ارسال نکنید.
- مسیرهای پیام، عکس، ویدیو، موزیک، AI و اثرانگشت صوتی با این پرچم به Local API تغییر می‌کنند.
- سقف آپلود Local Bot API برابر 2000 مگابایت است؛ فایل بزرگ‌تر باید جداگانه تبدیل یا محدود شود.
- قبل از انتقال: پشتیبان دیتابیس و کد، بررسی کلیدها، نصب باینری و start سرویس؛ سپس stop worker، cloud logOut و ایجاد پرچم، restart bot، getMe/getUpdates محلی و تست مسیرهای حساس.
- logOut ابری تا ۱۰ دقیقه بازگشت به cloud را غیرممکن می‌کند؛ بنابراین مراحل باید در یک پنجره مهاجرت و با کنترل سلامت انجام شوند.
- فایل حساس telegram_local.enabled باید در تنظیمات سرور باقی بماند، نه مخزن عمومی.