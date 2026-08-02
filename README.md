# Haz Market Data — REST Probe + File Logging

این پروژه علاوه بر Probe مستقل بازار، پایهٔ پایدار و مستقل از Transport برنامه را برای مدیریت هویت و وضعیت Signalist فراهم می‌کند. CLI بازار همچنان بدون تنظیمات یا اتصال دیتابیس اجرا می‌شود.

## کار فعلی پروژه

- دریافت لیست قراردادهای خطی USDT از Ourbit، Toobit و LBank
- نرمال‌سازی Symbolها
- استخراج اشتراک سه صرافی
- دریافت Last Price هر سه صرافی با REST هر ۵ ثانیه
- محاسبه Reference Price:
  - سه منبع سالم: Median
  - سه منبع با یک Outlier: Median و وضعیت `degraded`
  - دو منبع نزدیک: Average و وضعیت `degraded`
  - کمتر از دو منبع یا منابع ناسازگار: بدون Reference Price
- ذخیره خروجی‌های بررسی در فایل JSON
- حذف قیمت‌های قدیمی‌تر از ۱۵ ثانیه از محاسبه در زمان تحلیل
- تشخیص Snapshot ناقص و نگهداری وضعیت جاری هر صرافی در حافظه
- ثبت دائمی خطاها و وضعیت اجرا در فایل چرخشی `logs/market.log`

## وابستگی و پیکربندی

Python 3.11 یا جدیدتر لازم است. SQLite پایگاه دائمی محصول و Redis 7 فقط
ذخیره‌ساز وضعیت‌های موقت تلگرام (Lease Poller، Idempotency، FSM و هماهنگی کوتاه‌عمر)
است:

```bash
python -m pip install -r requirements.txt
cp .env.example .env
mkdir -p data
```

برنامه فقط در Entry Pointهای `main.py`، `python -m chainura.telegram` و Alembic
فایل `.env` ریشهٔ پروژه را صریحاً بارگذاری می‌کند. Import ماژول‌ها اثری بر محیط
ندارد و مقدار واقعی Process Environment همیشه بر `.env` اولویت دارد. نمونهٔ امن:

```text
DATABASE_URL=sqlite+aiosqlite:///./data/chainura.db
MAIN_ADMIN_TELEGRAM_ID=123456789
TELEGRAM_BOT_TOKEN=123456789:replace_me
REDIS_URL=redis://localhost:6379/0
```

فایل `.env`، Token و Credential نباید Commit یا Log شوند. مسیر نسبی SQLite از
Working Directory پروژه resolve می‌شود و پوشهٔ والد هنگام ساخت Engine ایجاد
می‌شود، نه هنگام Import تنظیمات.

## پایهٔ پایدار و اجرای دیتابیس

تنها موجودیت پایدار فعلی `Signalist` است. وضعیت‌های `active`، `inactive` و
`archived` با Soft Archive نگهداری می‌شوند. SQLite منبع دائمی حقیقت است و فایل
پیش‌فرض آن `data/chainura.db` روی دیسک محلی و پایدار قرار می‌گیرد؛ این فایل را
روی NFS، SMB یا Network Filesystem قرار ندهید.

این بازنویسی Baseline فقط به‌دلیل نبود Deployment و دادهٔ Production مجاز است.
دیتابیس‌های محلی PostgreSQL فاز ۲ دیگر استفاده نمی‌شوند و فایل SQLite توسعهٔ
قدیمی باید حذف و از Migration بازسازی شود:

```bash
mkdir -p data
alembic upgrade head
alembic current
alembic history
```

برای آزمایش چرخه روی یک فایل Disposable (نه فایل توسعه):

```bash
export DATABASE_URL="sqlite+aiosqlite:////tmp/chainura-migration-check.db"
alembic upgrade head
alembic current
alembic downgrade base
alembic upgrade head
rm -f /tmp/chainura-migration-check.db{,-wal,-shm}
```

هر Connection، Foreign Key، Busy Timeout پنج‌ثانیه‌ای، WAL و synchronous=NORMAL
را فعال و بررسی می‌کند. فایل‌های `.db`، `-wal` و `-shm` همگی Runtime Artifact
هستند و نباید Commit شوند. Transactionها کوتاه‌اند؛ هیچ Telegram/Exchange/Redis
Network Call، Sleep یا انتظار کاربر داخل Transaction دیتابیس انجام نمی‌شود.
Transition وضعیت با Update شرطی compare-and-swap انجام می‌شود.

## فاز ۴: مدیریت مدیر اصلی

مدیر اصلی اکنون می‌تواند سیگنالیست‌ها و مقصدها را ایجاد، فهرست، فیلتر و صفحه‌بندی
کند و وضعیت آن‌ها را با کنترل همزمانی تغییر دهد. مقصد شامل نام داخلی یکتا، Chat ID
تغییرناپذیر، نوع کانال/گروه، دسترسی عمومی/خصوصی، وضعیت فعال/غیرفعال و سقف روزانه است.
اولویت صرافی هر مقصد به‌صورت رابطه‌ای و یک زیرمجموعهٔ مرتب و غیرخالی از Ourbit،
Toobit و LBank ذخیره می‌شود. انتساب سیگنالیست‌ها مقصد-محور است؛ سیگنالیست فعال یا
غیرفعال قابل انتساب است، سیگنالیست بایگانی‌شده انتساب جدید نمی‌گیرد و انتساب قبلی
او حفظ می‌شود. هیچ اطلاعات مقصدی به رابط سیگنالیست نمایش داده نمی‌شود.

این فاز فقط پیکربندی را ذخیره می‌کند. شمارش/اعمال سقف روزانه، انتشار، قالب‌ها،
گزارش‌ها و هشدارهای قطعی در آن پیاده‌سازی نشده‌اند. برای اعمال Migration جدید:

ثبت مقصد با Chat ID آغاز می‌شود. ورودی‌هایی مانند `3147975662` و
`1003147975662` به `-1003147975662` نرمال می‌شوند. شناسهٔ منفی متعارف گروه‌های
معمولی، مانند `-123456789`، بدون تغییر حفظ می‌شود. ربات باید از قبل مدیر کانال
یا گروه باشد؛ نام و نوع مقصد از Telegram دریافت و پیش از انتخاب دسترسی، سقف
سیگنال روزانه و اولویت صرافی‌ها صریحاً تأیید می‌شود. هیچ پیام آزمایشی ارسال
نمی‌شود و سقف سیگنال روزانه در این فاز فقط پیکربندی است.

برای آزمون دستی، Redis را اجرا کنید، `python -m chainura.telegram` را آغاز کنید،
یک سیگنالیست را تنها با شناسه و نام بسازید و سپس ثبت مقصد را یک‌بار بدون دسترسی
مدیر و یک‌بار پس از مدیرکردن ربات امتحان کنید. در پایان، نمایش تاریخ جلالی با
زمان تهران، اولویت شماره‌دار عمودی، نبود شناسه‌های داخلی و ماندگاری پس از Restart
را بررسی کنید. Token یا Chat ID حساس را در Log یا Screenshot قرار ندهید.

```bash
alembic upgrade head
```

### اجرای تلگرام و Market Data

```bash
python -m chainura.telegram
python main.py symbols
python main.py watch --seconds 30
```

توسعه از Long Polling رسمی aiogram و Redis Poller Lease استفاده می‌کند. تنها یک
Process برنامه و یک Poller فعال فرض شده است. Shutdown عادی Poller و Redis را
می‌بندد و Engine را Dispose می‌کند تا WAL checkpoint/cleanup معمول SQLite انجام
شود. CLI بازار `.env` را بارگذاری می‌کند اما تنظیمات دیتابیس را مطالبه نمی‌کند و
در Automated Test هیچ Telegram API یا Live Exchange Call انجام نمی‌شود.

### تست‌ها

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s integration_tests -v
```

Integration SQLite برای هر اجرای ایزوله Temporary Directory و فایل واقعی خودش
را می‌سازد، Engineها را می‌بندد و فقط همان Directory را پاک می‌کند؛ از `:memory:`
یا دیتابیس توسعه استفاده نمی‌کند.

تست Redis فقط URL صریح با Database غیرصفر را می‌پذیرد و فقط Namespace تصادفی
خودش را حذف می‌کند:

```bash
export TEST_REDIS_URL='redis://localhost:6379/15'
python -c "from redis_integration_tests.support import require_safe_test_redis; require_safe_test_redis()"
python -m unittest discover -s redis_integration_tests -v
```

### Backup و Restore امن SQLite

هنگام Write فعال در WAL، فقط فایل `.db` را Copy نکنید. روش پشتیبانی‌شدهٔ ساده:
برنامه را Clean Stop کنید، از توقف Writer و بسته‌شدن Engine مطمئن شوید، سپس فایل
`data/chainura.db` را روی Storage امن Copy کنید. برای Backup آنلاین فقط از دستور
`.backup` ابزار SQLite یا Backup API رسمی استفاده کنید تا Snapshot سازگار شامل
WAL ساخته شود. Backupها باید منظم، خارج از Repository و قابل بازیابی آزمایش‌شده
باشند.

برای Restore، برنامه را متوقف کنید، فایل‌های قدیمی `chainura.db-wal` و
`chainura.db-shm` را پس از حفظ نسخهٔ ایمنی کنار بگذارید، فایل Backup را در
`data/chainura.db` قرار دهید، دسترسی فایل را بررسی کنید، `alembic current` را
اجرا و سپس برنامه را یک‌بار راه‌اندازی کنید.

### مرز عملیاتی و مهاجرت آینده

SQLite برای حدود ۱۰ Signalist، تا ۵۰ کانال و نزدیک ۴۰ Signal روزانه، با یک Process
برنامه، دیسک محلی پایدار و Backup منظم انتخاب شده است. PostgreSQL اکنون لازم
نیست. فقط در صورت نیاز به چند Instance برنامه، چند Worker همزمان، Write
Concurrency بالا، Hosting راه‌دور، Replication، Analytics بزرگ یا الزام عملیاتی
فراتر از Single-file Database باید دوباره بررسی شود. SQLAlchemy، Repository،
Service و Alembic جدا از Domain و Telegram باقی مانده‌اند تا مهاجرت آینده بدون
تغییر منطق Domain یا Transport ممکن باشد؛ فعلاً Dual-database Complexity نگهداری
نمی‌شود.

### بررسی معادل CI

```bash
python -m unittest discover -s tests -v
python -m compileall .
python main.py --help
python main.py symbols --help
python main.py watch --help
python -m unittest discover -s integration_tests -v
python -m unittest discover -s redis_integration_tests -v
git diff --check
```

## لاگ دائمی فایل

با اجرای هر دستور، پوشه `logs/` به‌صورت خودکار ساخته می‌شود و لاگ در این مسیر ذخیره می‌شود:

```text
logs/market.log
```

موارد ثبت‌شده شامل شروع و پایان برنامه، نتیجه Discovery، خلاصه سلامت هر Snapshot، خطاهای هر صرافی همراه Stack Trace و خطاهای Fatal برنامه است.

لاگ‌ها به‌صورت چرخشی نگهداری می‌شوند:

- حداکثر حجم هر فایل: ۱۰ مگابایت
- تعداد نسخه‌های قبلی: ۱۰ فایل
- فایل‌های قدیمی: `market.log.1` تا `market.log.10`
- اجرای مجدد برنامه فایل قبلی را پاک نمی‌کند و ادامه همان فایل نوشته می‌شود.

برای مشاهده زنده لاگ روی سرور:

```bash
tail -f logs/market.log
```

اجرای دائمی داخل Screen:

```bash
screen -S haz-market
python main.py watch
```

## فیلتر Numeric در LBank

LBank تعداد زیادی قرارداد با Base کاملاً عددی برمی‌گرداند. این موارد داخل خود Adapter حذف نمی‌شوند.

بعد از دریافت لیست هر سه صرافی:

- Numeric Symbolی که هم در Ourbit و هم Toobit وجود دارد حفظ می‌شود؛ مانند `4USDT`.
- Numeric Symbolی که فقط در LBank دیده می‌شود حذف می‌شود.

به این ترتیب قرارداد واقعی مشترک حذف نمی‌شود، اما Symbolهای داخلی و نامعمول LBank وارد لیست نهایی نمی‌شوند.

## تشخیص اختلاف قیمت

حد اختلاف پیش‌فرض ۵ درصد است و با `--max-spread` قابل تغییر است:

```powershell
python main.py --max-spread 3 symbols
```

اگر سه قیمت وجود داشته باشند و دو قیمت نزدیک اما سومی پرت باشد، Symbol حذف نمی‌شود. Median به‌عنوان Reference Price استفاده و منبع پرت ثبت می‌شود.

نمونه فعلی:

```text
1000XECUSDT
Ourbit ≈ 0.0061
Toobit ≈ 0.0061
LBank  ≈ 0.03442
Reference Price = Median ≈ 0.0061
Status = degraded
Outlier = lbank
```

## فایل‌های خروجی

داخل `output/`:

- `common_symbols.json`: اشتراک سه صرافی
- `symbol_mapping.json`: Symbol خام هر صرافی
- `symbol_diagnostics.json`: آمار کامل Discovery و Validation
- `price_ready_symbols.json`: Symbolهایی که Reference Price دارند
- `healthy_symbols.json`: سه منبع نزدیک
- `degraded_symbols.json`: قابل استفاده با منبع مفقود یا Outlier
- `reference_prices.json`: Reference Price، روش و وضعیت هر Symbol
- `price_mismatches.json`: اختلاف‌های نیازمند بررسی
- `missing_prices.json`: منبعهای فاقد قیمت
- `stale_prices.json`: قیمت‌های موجود اما قدیمی، جدا از قیمت‌های مفقود
- `symbol_collisions.json`: نگاشت‌های Canonical مبهم حذف‌شده در Discovery
- `lbank_ignored_numeric_symbols.json`: Numeric Symbolهای حذف‌شده LBank

قیمت دقیقاً ۱۵ ثانیه‌ای تازه محسوب می‌شود؛ قیمت قدیمی‌تر در Median، Average،
Outlier یا Reference Price شرکت نمی‌کند. وضعیت جاری صرافی‌ها (`unknown`،
`healthy`، `degraded` و `down`) فقط در حافظه نگهداری می‌شود. پاسخ موفق ولی
ناقص `degraded` است و Tickهای قبلی حذف نمی‌شوند تا با همین قانون زمان منقضی شوند.

## APIهای فعلی

- Ourbit instruments/prices: `GET https://futures.ourbit.com/api/v1/contract/ticker`
- Toobit instruments: `GET https://api.toobit.com/api/v1/exchangeInfo`
- Toobit prices: `GET https://api.toobit.com/quote/v1/contract/ticker/price`
- LBank instruments: `GET https://lbkperp.lbank.com/cfd/openApi/v1/pub/instrument?productGroup=SwapU`
- LBank prices: `GET https://lbkperp.lbank.com/cfd/openApi/v1/pub/marketData?productGroup=SwapU`

## ساختار پروژه

```text
haz-market-data-rest-logging/
├── exchanges/
│   ├── common.py
│   ├── ourbit.py
│   ├── toobit.py
│   └── lbank.py
├── tests/
├── http_client.py
├── models.py
├── logging_config.py
├── market.py
├── main.py
├── requirements.txt
└── README.md
```

## فاز 5A: ساخت سیگنال و قرارداد رندر

فاز 5A مدل پایدار سیگنال، دو مسیر نماد مشترک و قرارداد دقیق تک‌صرافی، اعتبارسنجی Decimal قیمت ورود با تلورانس دقیق `0.05%`، بازاعتبارسنجی نهایی، و ثبت اتمیک و idempotent را اضافه می‌کند. فقط سیگنالیست فعال از گزینهٔ پایدار `➕ ساخت سیگنال` استفاده می‌کند؛ پیش‌نویس فقط در FSM موقت Redis است و پس از تأیید، پیام فارسی کنترل‌شدهٔ ثبت موفق نمایش داده می‌شود. هیچ اطلاعات مقصدی نشان داده و هیچ انتشاری انجام نمی‌شود.

Migration را با دستور زیر اعمال کنید:

```bash
alembic upgrade head
```

قرارداد عمومی و immutable رندر در `chainura/rendering/` قرار دارد و dispatch صرافی، payload قیمت/زمان externally supplied و خطای کنترل‌شدهٔ renderer پیکربندی‌نشده را تعریف می‌کند. تولید واقعی تصویر، مختصات، فونت، asset و template عمداً در این فاز پیاده‌سازی نشده است؛ TP و SL برای caption آینده‌اند، نه کارت تصویر.

### مسیر کامل تلگرام در فاز 5A

مسیر سیگنالیست فعال اکنون جست‌وجو و صفحه‌بندی فهرست مشترک، مسیر قرارداد تأییدشدهٔ تک‌صرافی، انتخاب جهت، اعتبارسنجی اولیهٔ Entry، اهرم، تارگت‌های مرتب، SL، مرور نهایی و بازاعتبارسنجی بازار پیش از ثبت اتمیک را پوشش می‌دهد. Callbackهای این مسیر typed، ASCII و وابسته به Flow هستند و پاسخ دیررس جست‌وجو یا قیمت پس از لغو، `/start` یا بازگشت به منوی اصلی نمی‌تواند FSM را زنده یا سیگنال را ثبت کند. Runtime از `MarketProbe` و Adapterهای موجود برای Catalog و Reference Price استفاده می‌کند و معماری HTTP صرافی دیگری ندارد.

اندازه‌های نهایی Canvas به‌صورت Specification تغییرناپذیر در `chainura/rendering/models.py` تعریف شده‌اند: Ourbit برابر 1080×1148، Toobit برابر 942×1010 و LBank برابر 830×1005، همگی PNG. این مشخصات فقط Contract هستند؛ همچنان هیچ Pillow compositor، مختصات، فونت یا Template تولیدی وجود ندارد.
