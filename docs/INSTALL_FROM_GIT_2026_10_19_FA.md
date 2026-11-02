# نصب یکپارچه Masiha VPN Manager از GitHub — نسخه به‌روز ۲ نوامبر ۲۰۲۶

## پیش‌نیازهای سرور

- **Ubuntu Server 24.04 LTS نسخه amd64، کاملاً تازه** (بدون نصب قبلی x-ui). نصب‌کننده را روی سرور دارای مشتری اجرا نکنید.
- دسترسی root، توصیه حداقل ۴ گیگابایت RAM، ۲ هسته CPU و ۲۰ گیگابایت دیسک.
- دامنه با DNS نوع A که به IP همین سرور اشاره کند؛ TCP/80 و TCP/443 عمومی برای SSL و پنل.
- اینترنت جهت apt، GitHub، Go، Node.js و رجیستری‌های npm.
- در فایروال ارائه‌دهنده: UDP/1194 برای OpenVPN، TCP/UDP 4443 برای OpenConnect، TCP/8443–8448 و TCP/8450–8457 برای Xray، و UDP/8452–8455 برای Shadowsocks. اسکریپت UFW را تنظیم می‌کند ولی آن را اجباری فعال نمی‌کند. **پورت‌های داخلی 62053، 2096 و 61794 را عمومی نکنید.**

## نصب با Git

این دستورات را با SSH روی **سرور تازه** اجرا کنید. تا قبل از ادغام انتشار جدید در main، شاخه upgrade/3x-ui-v3.11.0-migration را Clone کنید. کلون Git اطلاعات کاربران یا کلیدهای سرور قبلی را منتقل نمی‌کند:

    sudo apt-get update
    sudo apt-get install -y git ca-certificates curl
    git clone --branch upgrade/3x-ui-v3.11.0-migration --single-branch https://github.com/masiweb/Masih-panel.git
    cd Masih-panel
    sudo bash deploy/install-ubuntu-24.04.sh

برای نصب بدون قطع سرویس فعلی از زیردامنه موقت استفاده کنید که DNS A آن به IP سرور جدید اشاره کند (مثلاً stage.example.com). اگر دامنه فعلی هنوز به سرور قبلی اشاره دارد، استفاده از همان دامنه در نصب جدید باعث شکست چالش Let’s Encrypt می‌شود. بعد از بازیابی، تغییر DNS و دامنه/گواهی را در مرحله Cutover انجام دهید.

دامنه (مثل stage.example.com) و ایمیل Let’s Encrypt را در ترمینال وارد کنید. روش غیرتعاملی:

    sudo env DOMAIN="stage.example.com" EMAIL="admin@example.com" \
      PANEL_ADMIN_USER="admin" bash deploy/install-ubuntu-24.04.sh

**مسیر پنل در این نسخه باید /xui/ باشد.** مسیر سفارشی سایر APIها را می‌شکند و نصب‌کننده آن را رد می‌کند.

## نسخه پایه و ابزار ساخت

- upstream: MHSanaei/3x-ui نسخه v3.11.0، commit c621ba9014a6aa21fd7c9ecf45655e44e24ebf13.\n- Go: 1.27.3؛ Node.js: 26.13.0 (مطابق go.mod و engines فرانت‌اند).\n- Patch شامل ۳۳ فایل سفارشی است. ساخت React/TypeScript و Go پیش از جایگزینی باینری انجام می‌شود.\n\n## اجزایی که نصب می‌شوند

۱. **پنل x-ui اختصاصی بر پایه نسخه پین‌شده 3.11.0 سنایی**، به‌همراه ۱۴ اینباند نمونه Xray، HTTPS و Subscription. نسخه پایه رسمی 3.11.0 است و Patch اختصاصی Masiha روی آن اعمال می‌شود؛ برای امکانات مخصوص صفحه اشتراک از باینری اصلاح‌شده استفاده می‌شود.

۲. OpenVPN و OpenConnect، ساخت حساب خودکار برای مشتری Xray، حسابداری ترافیک و انقضای مشترک، فایل‌های تنظیمات کاربر و صفحه Subscription باینری اختصاصی.

۳. ربات فروش تلگرام، مدیریت پلن‌ها، کیف پول، پرداخت کارت‌به‌کارت/تأیید رسید و امکانات پیاده‌سازی‌شده سرگرمی و امتیاز. برای فعال‌شدن واقعی ربات باید توکن BotFather و شناسه مدیر در پنل ثبت شوند.

۴. **بخش مستقل مدیریت مسیحا** در منوی کناری پنل، با تنظیمات ربات، OpenVPN، OpenConnect و بکاپ/بازیابی. منوی sidecar و صفحه مستقل /masiha-ui/ بیرون از باینری‌اند؛ اما routeها و APIهای native فروش/VPN/بکاپ داخل باینری Patch شده‌اند. به همین دلیل دکمه self-update رسمی پنل در این build عمداً مسدود است.

۵. بکاپ رمزنگاری‌شده و بازیابی با کلید مختص سرور، Nginx، SSL خودکار، سرویس‌های systemd و اطلاعات ورود تصادفی (کلیدها و توکن‌ها هرگز در Git نیستند).

**محدودیت مهم:** منوی تزریق‌شده Nginx و صفحه مستقل مسیحا با تعویض باینری باقی می‌مانند، ولی صفحات و APIهای native اختصاصی در صورت نصب باینری رسمی حذف می‌شوند. self-update پنل عمداً غیرفعال است؛ فقط از Git و build پین‌شده Masiha ارتقا دهید. پیش از آپدیت رسمی باید بکاپ گرفت و اتصال و خرید/تمدید کاربران را آزمایش کرد. همچنین WireGuard و Node Agent در بسته نصب یکپارچه فعلی نصب نمی‌شوند؛ جزو امکانات کاملِ تأییدشده این بسته نیستند.

## نمونه خروجی پایانی ترمینال

فقط بعد از موفقیت نصب تمام سرویس‌ها، صحت ورود، صفحه مستقل و منوی مسیحا، اطلاعات نمایش داده می‌شوند:

    MASIHA VPN MANAGER — COMPLETE INSTALLATION VERIFIED
    Main panel        : https://vpn.example.com/xui/
    Masiha management : https://vpn.example.com/masiha-ui/
    VPN subscription  : https://vpn.example.com/sub/
    Admin username    : admin
    Admin password    : <random password generated on this server>
    OpenVPN endpoint  : vpn.example.com:1194/udp
    OpenConnect       : vpn.example.com:4443/tcp+udp
    Recovery file     : /root/masiha-vpn-initial-login.env (root only)

رمز هر سرور به‌طور تصادفی ساخته می‌شود و در فایل روت /root/masiha-vpn-initial-login.env با مجوز 0600 ذخیره می‌شود. هیچ رمز، توکن، کلید SSL یا دیتابیس مشتری در مخزن عمومی قرار نمی‌گیرد.

## ادامه نصب پس از خطا

اگر سرویس‌های اصلی نصب شده‌اند ولی رابط مستقل شکست خورد، **نصب تازه را دوباره اجرا نکنید** و در مسیر همین Git:

    sudo bash deploy/finish-install.sh --resume

این فرمان گواهی CA، کلید SSL، رمز قبلی، دیتابیس و فایل اجرایی x-ui را از نو ایجاد نمی‌کند. اگر خطا در مرحله قبل یعنی finalization رخ داد و فایل .finalize-in-progress هنوز موجود است:

    sudo bash deploy/finalize-ubuntu-24.04.sh --resume

هیچ نشانگر امنیتی یا دیتابیس را برای دورزدن محافظ نصب حذف نکنید.

## تست سلامت

    sudo bash deploy/verify-sidecar-menu.sh
    sudo bash deploy/verify-install.sh
    sudo bash deploy/release-preflight.sh
    sudo systemctl status x-ui nginx masiha-vpn-sidecar masiha-vpn-extension \
      ocserv openvpn-server@masiha --no-pager

**وضعیت راستی‌آزمایی:** تست syntax، تست‌های ایزوله فروش/سهمیه و بکاپ، تست‌های جدید ترتیب نصب و rebuild رابط مستقل روی سرور موجود موفق بودند. **نصب صفر تا صد از Git روی یک VPS کاملاً تازه هنوز به‌شکل عملی اجرا نشده است** و تست اتصال واقعی VPN و تراکنش خرید نیازمند سرور و حساب آزمایشی هستند.

برای انتقال مشترکین قبلی باید دیتابیس‌ها، کلیدهای CA و گواهی‌ها از بکاپ رمزنگاری‌شده بازیابی شوند؛ clone گیت مشتریان قبلی را منتقل نمی‌کند.
