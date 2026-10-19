### اعلان خودکار موزیک، فیلم و سریال

سه کلید مستقل در مدیریت مسیحا، ربات، کتابخانه موزیک و فیلم وجود دارد.
با روشن‌بودن هر کلید، فایل جدید همان دسته برای کاربران فعال ارسال می‌شود.
فایل از تلگرام و از صف پایدار ارسال می‌شود؛ لینک دانلود نمایش داده نمی‌شود.
هر سه گزینه به‌صورت پیش‌فرض خاموش‌اند.

## به‌روزرسانی ربات: ویدیو و آرشیو RozMusic

نوع ویدیو (سینمایی یا سریال)، شماره فصل و قسمت برای سریال اجباری شد.
موزیک‌های مجاز از RozMusic به‌صورت موقت دانلود و با Telegram sendAudio آپلود می‌شوند؛ فقط شناسه فایل تلگرام، خواننده، آهنگ و متن ترانه در پلی‌لیست all ذخیره شده و هیچ لینک دانلودی به کاربر نشان داده نمی‌شود.
راهنمای استفاده: docs/ROZMUSIC_BOT_IMPORT_FA.md

---

## نصب یکپارچه از Git روی سرور تازه — ۱۹ اکتبر ۲۰۲۶

برای نصب تمام امکانات پیاده‌سازی‌شده (x-ui اختصاصی، Xray، OpenVPN، OpenConnect، فروش و ربات تلگرام، مدیریت مستقل مسیحا و بکاپ/بازیابی) روی Ubuntu Server 24.04 amd64 خالی:

    sudo apt-get update && sudo apt-get install -y git ca-certificates curl
    git clone https://github.com/masiweb/Masih-panel.git
    cd Masih-panel
    sudo bash deploy/install-ubuntu-24.04.sh

نصب‌کننده دامنه و ایمیل SSL را می‌پرسد و در انتهای نصب موفق **آدرس‌ها، نام کاربری و رمز تصادفی مدیر را در ترمینال نمایش می‌دهد**. برای ادامه امن مرحله نهایی، دستور زیر وجود دارد:

    sudo bash deploy/finish-install.sh --resume

راهنمای کامل: [نصب از گیت](docs/INSTALL_FROM_GIT_2026_10_19_FA.md).
این نصب در ابتدا از باینری سفارشی پین‌شده سنایی 3.8.5 استفاده می‌کند؛ پنل رسمی 3.10.2 نیست. منو و بخش مستقل مدیریت مسیحا در آپدیت رسمی حذف نمی‌شوند، ولی route اختصاصی پروفایل‌های صفحه اشتراک هنوز محدودیت سازگاری دارد.

---

# Masiha VPN Manager

Masiha VPN Manager is deployed as a **patched SANAei 3x-ui** with native Xray inbounds, OpenVPN, OpenConnect and a Telegram commerce manager. New deployments are generated from a pinned upstream commit plus our auditable patch and Python runtime sources.

**[راهنمای نصب کامل روی سرور جدید (فارسی)](docs/INSTALL_NEW_SERVER_FA.md)**

**[چک‌لیست انتشار و تست نصب روی سرور جدید](docs/NEW_SERVER_RELEASE_FA.md)**

**[بکاپ کامل رمزگذاری‌شده و بازیابی انتقال به سرور جدید](docs/FULL_BACKUP_RESTORE_FA.md)** — سه دیتابیس، کلاینت/اینباند/نود/ترافیک، ربات و پرداخت‌ها، OpenVPN/OpenConnect، گواهی و تنظیمات سرور؛ شامل صحت‌سنجی و تست بازیابی ایزوله.

**[۶ روش آزمایش سرعت Shadowsocks و VLESS + رفع خطای SS2022](docs/INBOUND_SPEED_TEST_FA.md)**

**[راهنمای کتابخانه موزیک/فیلم، پلی‌لیست و گیمیفیکیشن تلگرام](docs/MEDIA_LIBRARY_FA.md)**

## Deployment source of truth (2026-10-11)

- `deploy/3x-ui.patch`: patch for 30 files over upstream 3x-ui commit `7ef22f94c950ff09f0870e2295fa65ad5968742c`.
- `runtime/`: live Python extension, shared quota/expiry accounting, OpenVPN and OpenConnect provisioning, Telegram sales worker, safe test scripts.
- `deploy/build-3x-ui.sh`: reproduces patched React and Go build.
- `deploy/install-ubuntu-24.04.sh`: fresh Ubuntu 24.04 amd64 provisioning; **refuses pre-existing x-ui installations**.
- `deploy/bootstrap-inbounds.py`: six baseline Xray profiles; `deploy/extend-inbounds.py`: two modern XHTTP/H2 presets; `deploy/benchmark-inbounds.py`: six additional Shadowsocks/VLESS speed comparison inbounds. SS2022 is no longer enabled automatically because malformed per-client keys can prevent Xray startup.
- `deploy/finalize-ubuntu-24.04.sh`: safely recover partially completed installations and provision HTTPS subscriptions/default Xray inbounds.
- `deploy/verify-install.sh`: post-install service sanity, preset/subscription checks and isolated shared-quota/Telegram commerce tests.
- `docs/RELEASE_2026-10-11.md`: verification and known limits.

The older `app/` FastAPI control-plane and `node_agent/` prototype are retained for historical reference **but are not the implementation currently running on the VPN server**. Do not deploy that prototype as a replacement for the native extension.

## Supported flows

- Native 3x-ui Xray service with VLESS, VMess, Trojan and other supported inbounds.
- Shared per-client expiry and traffic across Xray, OpenVPN and OpenConnect, including automatically provisioned VPN credentials and downloadable profiles.
- Telegram bot sales with **admin-only Telegram management menus** (plans, customer access, coupons, top-ups, order reviews, support tickets, broadcasts, settings and failed-message retries), **phone-number verification toggle**, **web-only editable response templates**, full plan lifecycle (create, edit, duplicate, activate/deactivate, safe delete, sales/capacity metrics), **per-plan one-time free trials requiring no payment or wallet debit**, **customer-chosen subscription name before checkout with collision-safe 5-character random alphanumeric suffix**, a readable high-contrast RTL sales-management UI, receipt-based top-ups, discounted card payments with reversible coupon reservations, pending-order cancellation and admin approval, plus a visible failed-delivery queue and retry. Bot activation requires an actual token and admin Telegram IDs.
- Telegram-native user-submitted **music, film and TV series library**: media/forwarded file detection, direct HTTPS audio/video links, optional rich metadata and cover images, music and film playlists with public/private invitations, last 20, my media, search, likes, comments, creators leaderboard, copyright reports and verified publication. Actual Telegram media bytes remain on Telegram; the VPS stores file IDs and metadata only. See `docs/MEDIA_LIBRARY_FA.md`.
- Loyalty points and entertainment: configurable **purchase amount → points** and **successful referral → points**, exactly-once SQLite ledger, admin Telegram profile-photo card and customer subscription list, always-available Back/Home navigation, entertainment menus for AI/music/film/games, and optional point-funded AI requests via web-configured HTTPS Chat Completions API. Music, film and games are navigation placeholders; external content providers are not connected yet. AI is disabled until API credentials are configured.
- Automatic HTTPS subscription URI and per-protocol OpenVPN/OpenConnect profiles, with TLS termination by Nginx. Fresh installs include six original Xray presets, VLESS XHTTP stream-up/packet-up over TLS/H2, four classic AEAD Shadowsocks speed-test variants, plus VLESS gRPC/TLS and HTTPUpgrade/TLS (14 safe inbounds). SS2022 is disabled until proper per-client key validation is implemented. Prefer modern XHTTP where supported. No configuration guarantees access through every ISP filtering method.

Never commit customer profiles, live databases, private keys, passwords, bot tokens or production configuration. The public repository contains only source code, templates and instructions. See installation guide for the complete list of backup-only paths. For the Telegram bot development and plan-management walkthrough see [راهنمای مدیریت ربات و پلن‌ها](docs/TELEGRAM_BOT_FA.md).
