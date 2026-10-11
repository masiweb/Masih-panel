# نصب کامل Masiha VPN Manager روی سرور تازه (۱۱ اکتبر ۲۰۲۶)

این راهنما برای **Ubuntu 24.04 x86_64 تازه و بدون نصب قبلی x-ui** است. نصب موجود را با این اسکریپت ارتقا ندهید. منبع نسخه اجرایی، fork پین‌شده SANAei 3x-ui به‌اضافه `deploy/3x-ui.patch` و سرویس‌های `runtime/` است؛ کد FastAPI قدیمی در `app/` نسخه نصب‌شده تولیدی نیست.

## ۱. پیش‌نیازها

- حداقل پیشنهادی: ۲ vCPU، ۴ GB RAM، ۲۰ GB دیسک؛ دسترسی root/sudo، دسترسی خروجی به GitHub، go.dev، nodejs.org، npm و apt.
- رکورد DNS نوع A برای دامنه جدید (مثلاً `vpn.example.com`) باید به IP سرور برسد؛ TCP/80 جهت دریافت گواهی باز باشد.
- در فایروال میزبان/ارائه‌دهنده: TCP/22 (SSH)، TCP/80 و TCP/443، UDP/1194 (OpenVPN)، TCP و UDP/4443 (OpenConnect). پورت‌های اختصاصی Xray را فقط بعد از تعریف inbound باز کنید. **پورت مدیریتی 62053 را عمومی نکنید.**
- اسکریپت هیچ کلید خصوصی، تنظیمات ربات یا رمز عبوری را از GitHub دریافت نمی‌کند.

## ۲. نصب روی سرور جدید

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
git clone https://github.com/masiweb/Masih-panel.git
cd Masih-panel
git log -1 --oneline
sudo env DOMAIN="vpn.example.com" EMAIL="admin@example.com" \
  PANEL_PATH="/xui/" PANEL_ADMIN_USER="admin" \
  bash deploy/install-ubuntu-24.04.sh
```

نام دامنه و ایمیل را جایگزین کنید. این دستور **تنها روی سیستم تازه** مجاز است؛ اگر `/etc/x-ui/x-ui.db` یا x-ui نصب باشد، برنامه به‌جای بازنویسی آن متوقف می‌شود.

عملیات نصب: دریافت Go 1.27.1 و Node 24.21.0، واکشی upstream از `MHSanaei/3x-ui` در commit `7ef22f94c950ff09f0870e2295fa65ad5968742c`، اعمال patch اختصاصی، typecheck و build؛ نصب وابستگی‌ها و Xray با نصب‌کننده upstream؛ جایگزینی باینری اختصاصی؛ دریافت SSL با Certbot؛ تنظیم Nginx و پنل loopback؛ تولید CA اختصاصی EasyRSA و کانفیگ OpenVPN؛ پیکربندی ocserv؛ فعال‌سازی سرویس مشترک سهمیه/انقضا/ربات.

رمز اولیه پنل به‌صورت تصادفی روی **همان سرور** ایجاد و فقط در `/root/masiha-vpn-initial-login.env` با دسترسی root ذخیره می‌شود:

```bash
sudo cat /root/masiha-vpn-initial-login.env
```

پنل: `https://vpn.example.com/xui/`. پس از ورود، رمز را تغییر دهید؛ در این حالت نسخه root-only فایل `/etc/x-ui/install-result.env` باید نیز با اعتبارنامه جدید هماهنگ شود، چون سرویس فروش برای ورود محلی به پنل از آن استفاده می‌کند. رمز را در Git یا چت نفرستید.

## ۳. تنظیمات نهایی از داخل پنل

۱. در بخش Subscription تنظیم کنید: Enable = روشن، Listen IP = `127.0.0.1`، Port = `2096`، آدرس عمومی `https://vpn.example.com/sub`، و مسیر پیش‌فرض `/sub/` (برای پراکسی Nginx). در غیر این صورت لینک اشتراک و تحویل از ربات کامل نخواهد بود.
۲. از بخش Inbounds، ورودی‌های Xray موردنظر (VLESS REALITY، WS/TLS، Trojan یا سایر امکاناتی که انتخاب می‌کنید) را تعریف کنید و پورت آن‌ها را در فایروال باز کنید. تغییرات این ورودی‌ها وابسته به دامنه/گواهی و انتخاب‌های کاربر هستند و نباید با تنظیمات آزمایشی قدیمی کپی شوند.
۳. در قسمت ربات فروش، توکن BotFather، شناسه مدیران، پلن‌ها، درگاه و شماره کارت را در UI وارد کنید؛ بدون توکن و مدیر ربات فعال نمی‌شود. برای واریز کارت‌به‌کارت، اطلاعات تسویه و تأیید دستی رسید نیز باید تکمیل شوند.
۴. کاربر جدید را در پنل ایجاد و لینک ساب او را باز کنید. پردازشگر مشترک باید حساب‌های OpenVPN و OpenConnect را برای همان مشتری تولید کند؛ حجم و تاریخ انقضا از اشتراک Xray خوانده می‌شود، نه سهمیه مستقل. فایل OpenVPN و اطلاعات OpenConnect در صفحه اشتراک / VPN Profiles قرار می‌گیرند.

**نکته:** اتصال واقعی از اینترنت خارجی، QR code، فایل‌ها، ترافیک مشترک و دریافت پیام ربات را با حساب آزمایشی بررسی کنید. عبور تست‌های کامپایل جای این تست‌ها را نمی‌گیرد.

## ۴. آزمون، لاگ و عیب‌یابی

```bash
cd ~/Masih-panel
sudo bash deploy/verify-install.sh
sudo systemctl status x-ui ocserv openvpn-server@masiha masiha-vpn-extension --no-pager
sudo journalctl -u masiha-vpn-extension -n 80 --no-pager
sudo nginx -t
sudo ocserv -t -c /etc/ocserv/ocserv.conf
sudo certbot certificates
sudo ufw status
```

`deploy/verify-install.sh` فقط تست‌های SQLite ایزوله، syntax/config و وضعیت سرویس را بررسی می‌کند. تست `runtime/test_unified_live.py` جزو نصب نیست و نباید بدون برنامه در تولید اجرا شود؛ تست‌های تونل واقعی ممکن است کلاینت آزمایشی بسازند و مصرف ثبت کنند.

## ۵. اطلاعاتی که باید خارج از Git بکاپ گرفته شوند

```
/etc/x-ui/x-ui.db
/etc/x-ui/install-result.env
/etc/masiha-vpn-extension/config.json
/etc/masiha-vpn-extension/users.db
/etc/masiha-vpn-extension/sales/sales.db
/etc/openvpn/masiha/pki/
/etc/openvpn/masiha/tls-crypt.key
/etc/ocserv/masiha.passwd
/etc/letsencrypt/
```

برای مهاجرت سرور موجود، حفظ کلیدهای CA، دیتابیس فروش و دیتابیس پنل ضروری است. **نصب تازه به‌تنهایی مشترکان موجود را منتقل نمی‌کند.** بدون بکاپ و برنامه تطبیق دامنه و PKI فایل‌های موجود را جایگزین نکنید. برای انتقال کامل قبل از قطع سرور قدیمی، از SQLite با حالت backup یا توقف سرویس‌ها پشتیبان بگیرید و سپس کل پوشه‌های محرمانه را از طریق کانال امن منتقل کنید؛ سورس عمومی Git شامل آنها نیست.

## ۶. به‌روزرسانی

آپدیت مستقیم رسمی 3x-ui ممکن است routeهای فروش و OpenVPN/OpenConnect را از باینری حذف کند. برای نسخه اختصاصی تنها از patch داخل همین Git و ساخت مجدد استفاده کنید:

```bash
cd ~/Masih-panel
git pull --ff-only origin main
sudo env MASIHA_BUILD_ROOT="/opt/masiha-vpn-build-next" bash deploy/build-3x-ui.sh
# پس از بکاپ x-ui.db و باینری فعلی و تایید تست‌ها، باینری را با فرآیند تغییر نسخه جایگزین کنید.
```

اسکریپت ساخت عمداً هیچ نسخه موجودی را خودکار بازنویسی یا restart نمی‌کند. برای migration/upgrade از راهنمای مستقل و بکاپ استفاده کنید.

## ۷. محدودیت‌های صادقانه نسخه

- این بسته روی Ubuntu 24.04 amd64 طراحی شده و روی یک کپی تمیز سورس، build و typecheck شده است؛ اجرای installer یک‌پارچه روی سرور **کاملاً تازه** هنوز تست نشده است.
- Xray و اجزای رسمی از نصب‌کننده upstream دریافت می‌شوند؛ باینری پنل از patch پین‌شده بازسازی می‌شود، بنابراین نیازمند اتصال اینترنت هنگام نصب است.
- آدرس فعلی `xu.chanelchat.ir` متعلق به سرور قدیمی است؛ برای سرور جدید بدون تغییر DNS همان دامنه را استفاده نکنید.
