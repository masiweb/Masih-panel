# OpenVPN TCP برای MikroTik RouterOS 6.48.6

این قابلیت یک سرویس OpenVPN مستقل با TCP/1195 ایجاد می‌کند. تنظیمات سرویس قبلی UDP/1194 و کلیدهای TLS-Crypt آن دست‌نخورده می‌مانند.

## نصب روی سرور فعال

از شاخه main به‌روز شده استفاده کنید و فقط نصب‌کننده افزایشی زیر را اجرا کنید. **نصب‌کننده اصلی Ubuntu یا finalize را روی سرور فعال اجرا نکنید.**

sudo bash deploy/install-routeros6-openvpn.sh

سرویس جدید با OpenVPN 2.6، رمزنگاری AES-256-CBC، احراز اصالت HMAC-SHA1، TLS 1.2، گواهی RSA، نام کاربری و رمز عبور و بدون tls-crypt ساخته می‌شود. فایل CA و کانفیگ RouterOS از «اشتراک مشترک → پروفایل‌های VPN» دانلود می‌شود.

روی MikroTik، فایل masiha-ros6-ca.crt را در WinBox → Files آپلود کنید، سپس دستورات فایل mikrotik-USER.rsc را پس از بازبینی وارد ترمینال روتر کنید. مقدار add-default-route=no است تا مسیر اصلی اینترنت روتر به‌طور ناخواسته تغییر نکند.

این سرویس همان حساب مشترک x-ui را به کار می‌گیرد و مصرف را در client_traffics همراه سایر پروتکل‌ها ذخیره می‌کند. کاربران مجزا یا سهمیه جداگانه ایجاد نمی‌شود.

## کنترل

sudo systemctl is-active openvpn-server@masiha-ros6.service
sudo ss -ltn '( sport = :1195 )'
sudo journalctl -u openvpn-server@masiha-ros6.service -n 30 --no-pager

پورت 1195/tcp باید از اینترنت به سرور دسترسی داشته باشد. تست‌های واحد و راه‌اندازی سرویس روی سرور انجام شده‌اند؛ تست اتصال با یک RouterOS 6 واقعی فقط وقتی روتر آنلاین و دسترسی آن موجود باشد امکان‌پذیر است.

پیش از اجرای کانفیگ روتر، فایل RSC را محرمانه نگه دارید زیرا رمز اشتراک داخل آن است. برای RouterOS 6 از cipher=aes256، auth=sha1 و tls-version=only-1.2 استفاده می‌شود. HMAC-SHA1 صرفاً برای سازگاری با نسخه قدیمی به‌کار رفته است.