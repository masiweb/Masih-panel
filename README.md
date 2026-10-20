## ورود امن مستقل از مسیر ثابت سنایی
نشست مدیر در مسیر فعلی و واقعی x-ui بررسی می‌شود؛ ورود از پنل مسیحا به پچ ثابت سنایی یا مسیر SUB وابسته نیست و احراز هویت و CSRF لازم‌اند.

## طرح جستجوی موزیک با نمونه صوت
موتور تشخیص صوتی audfprint با اثرانگشت‌گذاری تدریجی آرشیو و دریافت Voice فعال شده است. راهنما: docs/VOICE_MUSIC_IDENTIFICATION_FA.md

## 🌐 کلاب ناشناس
پرونده کامل کاربران و نمایندگان در پنل فروش با کلیک روی نام حساب؛ مدیر از هزینه امتیازی چت و هوش مصنوعی معاف است.

چهار عملیات مدیریتی افزایش/کاهش امتیاز و کیف پول زیر پروفایل هر کاربر، با تأیید، تاریخچه و اعلان کاربر اضافه شده است.\nتعرفه شروع انواع گفتگو (رندوم، فیلتر، جستجو، مکان، دنبال‌شوندگان و دنبال‌کنندگان)
از پنل مدیریت جداگانه قابل تنظیم است. کسر امتیاز فقط پس از اتصال موفق و یک‌بار
در هر گفتگو است؛ پیام‌های بعدی رایگان هستند.
پروفایل با شناسه عمومی غیرتلگرامی، ۱۰ علاقه‌مندی، پنج عکس، دنبال‌کردن، بازدید پروفایل، چت ناشناس/دوستانه، مکان اختیاری، مدال و واکنش‌های لایک/دیسلایک. مدیریت و گزارش گفتگوها از وب. راهنما: docs/MASIHA_SOCIAL_FA.md

### دریافت اجباری شماره حساب تلگرام

با روشن‌کردن کلید «دریافت اجباری شماره حساب تلگرام»، **تمام کاربران جدید و قبلی بدون شماره**
فقط دکمه رسمی ارسال مخاطب خودشان را مشاهده می‌کنند. شماره تایپی یا مخاطب شخص
دیگر پذیرفته نمی‌شود. اعلان درخواست شماره برای اعضای قبلی نیز یک‌بار
ارسال می‌شود. تا زمان تأیید شماره، تمام منوها، خرید، رسانه، هوش مصنوعی،
خدمات مالی و حتی اعلان‌های در صف آن حساب غیرفعال/متوقف می‌شوند؛ پس از
ارسال مخاطب متعلق به خود شخص، استفاده از ربات آزاد خواهد بود.

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


## بازیابی امن سفارش‌های تأییدشده (اکتبر ۲۰۲۶)
- چنانچه اینباندهای 3x-ui حذف یا تغییر کنند، پلن‌ها و سفارش‌های قبلی نباید به شناسه‌های حذف‌شده وابسته بمانند.
- در لحظه خرید، پیش از برداشت وجه، لیست انتخابی پلن با اینباندهای فعال واقعی تطبیق می‌شود. اگر هیچ اینباندی باقی نمانده باشد، خرید رد شده و پولی کسر نمی‌شود.
- در تحویل سفارش‌های موجود، تنها اینباندهای هنوز فعال از انتخاب اصلی استفاده می‌شوند. اینباند جدید بدون انتخاب مدیر به پلن اضافه نمی‌شود.
- سفارش تأییدشده‌ای که ساخت آن به مشکل بخورد، به حالت نیازمند بررسی می‌رود، خطای طبقه‌بندی‌شده بدون افشای رمز در provision_failures ثبت می‌شود و با وقفه افزایشی حداکثر ۷ تلاش دارد؛ هیچ برداشت جدیدی برای تلاش مجدد انجام نمی‌شود.
- مدت و حجم سرویس در اولین تلاش به‌صورت هدف ثابت ذخیره می‌شود تا تلاش بعدی زمان و حجم اضافه نکند. پیام تحویل با شناسه یکتا ثبت می‌شود و از اطلاع‌رسانی تکراری جلوگیری می‌کند.
- repair_stale_inbound_plans صرفاً لیست پلن‌های فروش را هماهنگ می‌کند؛ سفارش‌های تأییدشده قبلی و تراکنش‌ها را بازنویسی نمی‌کند.
- برای عیب‌یابی: SELECT order_id,attempts,last_error,last_attempt,resolved FROM provision_failures؛ هیچ رمز یا کلید اتصال در این جدول ثبت نمی‌شود.

## فروش هوش مصنوعی داخل ربات
خرید دو شاخه VPN و هوش مصنوعی؛ سه حالت داخل ربات، ایمیل شخصی و کد آماده؛ مدیریت مدل‌ها، پلن‌ها و رسیدها در تب «فروش هوش مصنوعی». راهنما: docs/AI_SALES_FA.md

## یکپارچگی ورود پنل مسیحا و سنایی — ۲۰ اکتبر ۲۰۲۶
آدرس اصلی مدیریت: https://s2.chanelchat.ir/masiha-ui/#bot . صفحه قدیمی فروش سنایی به همین آدرس هدایت می‌شود و منوی تکراری مخفی است؛ APIهای مشترک به‌دلیل استفاده پنل اصلی حفظ شده‌اند.
کوکی ورود سنایی با Path محدود به مسیر سنایی باقی می‌ماند و برای صفحه مسیحا کپی یا عمومی نمی‌شود. صفحه مسیحا نشست را روی آدرس معتبر خود سنایی و با بررسی CSRF سمت سرور تأیید می‌کند. تغییر مسیر محرمانه سنایی از مسیر زنده دیتابیس پیروی می‌کند؛ پس از تغییر مسیر، یک‌بار صفحه مسیحا را از منوی پنل سنایی باز کنید.

## بررسی و بازیابی استارت تلگرام (۲۰ اکتبر ۲۰۲۶)
- ترتیب الزامات ورود: ابتدا عضویت کانال، سپس ثبت شماره متعلق به حساب تلگرام، سپس منوی اصلی. راهنمای عضویت (`channel-gate:*`) و دکمه تماس (`phone-gate:*`) حتی برای کاربر بدون شماره ارسال می‌شوند؛ هیچ پیام سرویس/رسید/اطلاعات خصوصی پیش از ثبت شماره از این محدودیت عبور نمی‌کند.
- خطاهای موقت دریافت‌شده در `bot_retry_updates` با داده کامل update ذخیره می‌شوند و offset تلگرام تنها بعد از ثبت پایدار جلو می‌رود. تلاش مجددِ مستقل مانع توقف استارت کاربران جدید پشت یک درخواست ناموفق می‌شود. ردیف پس از موفقیت/خطای اعتبارسنجی پاک می‌شود. `last_error` تعداد درخواست‌های در انتظار را نشان می‌دهد. این جدول ممکن است موقتاً حاوی اطلاعات پیام خصوصی باشد: دسترسی به دیتابیس محدود و بکاپ باید حفاظت شود.
- برای بازیابی پیام‌های قدیمی و ارسال‌نشده راهنمای عضویت، پس از بکاپ از `python3 deploy/recover-start-prompts.py` استفاده کنید. این اسکریپت فقط متن دقیق راهنمای کانال را برای کاربران بدون شماره بازیابی می‌کند و موارد تکراری را کنار می‌گذارد؛ پیام‌های خصوصی دیگر همچنان پشت الزام شماره باقی می‌مانند.
- کنترل سلامت: `systemctl is-active masiha-vpn-extension`، `SELECT COUNT(*) FROM bot_retry_updates;` و `SELECT done,COUNT(*) FROM outbox GROUP BY done;`. برای تست خودکار: `cd runtime && python3 -m unittest -q test_sales.py`.

## حافظه گفتگو، خروجی PDF/TXT و اعلان عضو تازه — ۲۰ اکتبر ۲۰۲۶
- گفتگوهای هوش مصنوعی در جدول‌های افزودنی `ai_chat_history` و `ai_long_requests` ذخیره می‌شوند. هر کاربر تاریخچه مستقل دارد. برای جلوگیری از افزایش هزینه/حجم پیام API، حداکثر ۲۲ پیام اخیر تا سقف ۱۲٬۵۰۰ کاراکتر به مدل متن ارسال می‌شود؛ **تمام پیام‌های جدید موفق** در دیتابیس باقی می‌مانند تا کاربر با «🗑 پاک‌کردن سابقه» حذف‌شان کند. سابقه API قبلی که ذخیره نشده است قابل بازیابی نیست.
- درخواست‌های طولانی یا دارای کلیدواژه‌های متن مفصل، پیش از فراخوانی API پیشنهاد «PDF و TXT» یا «پاسخ در گفتگو» دریافت می‌کنند. فقط پس از تأیید، پاسخ تولید و یک بار سهمیه/امتیاز ثبت می‌شود. خروجی TXT با UTF-8 BOM برای خوانایی فارسی و PDF با صفحه‌آرایی راست‌به‌چپِ Pillow/libraqm ساخته می‌شود. PDF تصویری است؛ متن قابل جستجو و کپی در TXT همراه تحویل داده می‌شود. فایل‌ها تا زمان ارسال در صف ذخیره و بعد از ارسال موفق از صف حذف می‌شوند.
- اعلان اولین تماس کاربر تازه، با کلید یکتای `new-user:<uid>:<admin>` و در ابتدای پردازش `/start` یا اولین پیام ثبت می‌شود؛ حتی اگر کاربر هنوز عضو کانال نباشد یا شماره نداشته باشد. این اعلان فقط برای شناسه‌های مدیر تنظیم‌شده و در صورت فعال بودن کلید «اعلان کاربر جدید» ارسال می‌شود؛ هیچ اطلاعات شماره‌ای برای کاربری که شماره نداده، جعل یا فرض نمی‌شود.
- وابستگی نصب تازه: `python3-pil` همراه با پشتیبانی `libraqm` و فونت‌های `fonts-dejavu-core`. فایل `runtime/ai_conversation.py` باید همراه سایر ماژول‌های فروش نصب شود. تست: `cd runtime && python3 -m unittest -q test_ai_conversation.py`.
