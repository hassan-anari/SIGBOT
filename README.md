# Haz Market Data — REST Probe + File Logging

## WEEX futures support

WEEX is the fourth futures market. Chainura uses only Contract API V3 at
`api-contract.weex.com`: catalog discovery intersects
`/capi/v3/market/exchangeInfo` and `/capi/v3/market/apiTradingSymbols`, and each
normal poll makes one bulk `/capi/v3/market/premiumIndex` request using
`markPrice`. There is no per-Signal market request.

Shared eligibility remains the Ourbit/Toobit/LBank core intersection. WEEX may
enrich newly created Composite Signals with a fourth durable mapping and price;
historical three-source mappings are unchanged. WEEX is also supported for
single-exchange Market and Limit Signals, destination priorities, publication,
monitoring, templates, rendering, diagnostics, and durable health status.

The centralized 875x969 WEEX renderer is a deterministic functional first
pass. Final visual positioning is intentionally tuned through Renderer Lab.

## Production Upgrade Compatibility

The Phase 1–4 deployment gate preserves open `PRE_TP1` and `POST_TP1` trades
without reconstructing weighted history. Legacy unresolved TP decision messages
are upgraded to the Phase-4B interaction exactly once; historical approved or
declined TP decisions and terminal trades are not rewritten. `PENDING_DETAILS`
signals remain resumable through **Active Signals → Continue**. Open legacy
Breakeven prompts are retired without being converted into Risk Free actions.
An unresolved pre-4B final TP already stored as `COMPLETED_TARGETS` remains
terminal and receives only the historical Publish / Decline decision UI; it
never receives position actions or reconstructed accounting.

Ambiguous destination deliveries are never automatically resent. If a final TP
is already confirmed and its owner becomes inactive or archived, reconciliation
closes the remaining accounting volume without destination publication and
releases its trade locks. An unmonitorable active legacy trade remains visible,
is explicitly labelled as unavailable for automatic monitoring, and can be
cancelled through the normal durable cancellation path.

Lifecycle-only inactive-final cleanup also runs from the direct-notification
sweep, so disabling market monitoring does not strand an already-confirmed final
TP or require any exchange request.

Recommended production rollout (never run schema migration concurrently with
live application writers):

1. Stop application workers and polling cleanly.
2. Take and verify a SQLite backup.
3. Run `alembic upgrade head`.
4. Start the application.
5. Let compatibility reconciliation finish before normal monitoring activity.
6. Inspect the privacy-neutral compatibility summary counts in application logs.
7. Compare open-signal and trade-lock counts with pre-deployment expectations.
8. Only then consider the deployment healthy.

## Production initial rendering

Configure `RENDER_TEMPLATE_ASSET_ROOT`, `RENDER_OUTPUT_ROOT`,
`TOOBIT_COIN_LOGO_ROOT`, all four `RENDER_FONT_{REGULAR,MEDIUM,SEMIBOLD,BOLD}_PATH`
values, and positive `RENDER_CONCURRENCY`. Fonts and managed assets belong on
persistent local disk and are never committed. Templates are immutable
SHA-256-addressed PNGs; SQLite points to the active default or destination
override. Resolution prefers an override and falls back only when it is absent.
A missing or corrupt override is a hard failure.

Future admin upload handlers pass downloaded bytes—not filenames—to
`TemplateService.install`. Production composition is local and network-isolated.
`InitialImageService` renders off-loop under a bounded semaphore. Its caller owns
the returned short-lived output and must invoke the service's safe, idempotent
`cleanup()` after publication or abandonment. Renderer Lab overrides are never
accepted by production APIs.

Template uploads are limited to `MAX_TEMPLATE_BYTES` (currently 12 MiB).
Template verification, hashing, durable writes, local-logo inspection, and Pillow
composition are executed off the asyncio event loop under configured bounds.
The application owner must call `InitialImageService.aclose()` during shutdown;
Phase 5C publication will own this service and must also clean each returned image.
Shutdown ordering is: stop accepting publication work, cancel and await publication
tasks, await `InitialImageService.aclose()`, then close renderer registration.

این پروژه علاوه بر Probe مستقل بازار، پایهٔ پایدار و مستقل از Transport برنامه را برای مدیریت هویت و وضعیت Signalist فراهم می‌کند. CLI بازار همچنان بدون تنظیمات یا اتصال دیتابیس اجرا می‌شود.

## کار فعلی پروژه

- دریافت فهرست هستهٔ مشترک از Ourbit، Toobit و LBank و غنی‌سازی اختیاری با WEEX
- نرمال‌سازی Symbolها
- حفظ اشتراک سه صرافی هسته؛ نبود قرارداد WEEX نماد مشترک را حذف نمی‌کند
- دریافت قیمت‌های سه منبع هسته و WEEX MARK به‌صورت REST گروهی؛ بدون درخواست جداگانه برای هر سیگنال
- محاسبه Reference Price:
  - دو منبع هم‌نظر: Average
  - سه منبع: Median با حفاظت agreement/outlier
  - چهار منبع هم‌نظر: Average دو قیمت میانی؛ یک Outlier یکتا: Median سه منبع باقی‌مانده
  - کمتر از دو منبع یا بدون quorum یکتا: بدون Reference Price
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

### Phase 4A: پایهٔ داخلی حسابداری موقعیت

Phase 4A برای هر سیگنال ACTIVE باز، سهم باقی‌ماندهٔ دقیق Decimal را از `1` تا
`0` در SQLite نگه می‌دارد. هر بستن، بازده وزن‌دار
`closed_fraction × leveraged_roi` را بدون گردکردن زودهنگام به دفتر حسابرسی
اضافه می‌کند. Save50 فقط یک‌بار نصف **مقدار باقی‌ماندهٔ همان لحظه** را می‌بندد؛
Risk Free نیز فقط یک‌بار فعال می‌شود، حجم را نمی‌بندد و Effective Stop را به Entry
تغییر می‌دهد. این دو قابلیت مستقل‌اند. این فاز فقط زیرساخت داخلی، CAS نسخه‌دار و
بازسازی idempotent سیگنال‌های باز را فراهم می‌کند؛ دکمه، متن، رفتار مانیتورینگ یا
انتشار جدید تا Phase 4B فعال نشده است.

موجودیت‌های `Signalist`، مقصدها، انتساب‌ها، اولویت‌ها و Signalها به‌صورت دائمی در SQLite نگهداری می‌شوند. وضعیت‌های `active`، `inactive` و
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
Toobit، LBank و WEEX ذخیره می‌شود. اولویت‌های موجود خودکار تغییر نمی‌کنند و افزودن
WEEX فقط با ویرایش صریح مدیر اصلی انجام می‌شود. انتساب سیگنالیست‌ها مقصد-محور است؛ سیگنالیست فعال یا
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

- `common_symbols.json`: اشتراک سه صرافی هسته (Ourbit/Toobit/LBank)
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
- WEEX catalog: Contract API V3 `exchangeInfo` intersected with `apiTradingSymbols`
- WEEX prices: bulk Contract API V3 `premiumIndex`, using `markPrice`

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

مسیر فعلی Market هنگام انتخاب Symbol مقدار P0 و هنگام ایجاد نهایی P1 تازه را ثبت می‌کند؛ Drift دقیق P0→P1 حداکثر `0.70%` است و Reference پذیرفته‌شده Entry معتبر Market می‌شود. Stage Two در `PENDING_DETAILS` ادامه می‌یابد. مسیر Limit نیز Entry درخواستی Decimal را طبق بخش Limit پایین‌تر حفظ می‌کند. فقط سیگنالیست فعال از گزینهٔ پایدار `➕ ساخت سیگنال` استفاده می‌کند؛ پیش‌نویس فقط در FSM موقت Redis است و پس از تأیید، پیام فارسی کنترل‌شدهٔ ثبت موفق نمایش داده می‌شود. هیچ اطلاعات مقصدی نشان داده و هیچ انتشاری انجام نمی‌شود.

Migration را با دستور زیر اعمال کنید:

```bash
alembic upgrade head
```

قرارداد عمومی و immutable رندر در `chainura/rendering/` قرار دارد و dispatch صرافی، payload قیمت/زمان externally supplied و خطای کنترل‌شدهٔ renderer پیکربندی‌نشده را تعریف می‌کند. تولید واقعی تصویر، مختصات، فونت، asset و template عمداً در این فاز پیاده‌سازی نشده است؛ TP و SL برای caption آینده‌اند، نه کارت تصویر.

### مسیر کامل تلگرام در فاز 5A

مسیر سیگنالیست فعال اکنون جست‌وجو و صفحه‌بندی فهرست مشترک، مسیر قرارداد تأییدشدهٔ تک‌صرافی، انتخاب جهت، اعتبارسنجی اولیهٔ Entry، اهرم، تارگت‌های مرتب، SL، مرور نهایی و بازاعتبارسنجی بازار پیش از ثبت اتمیک را پوشش می‌دهد. Callbackهای این مسیر typed، ASCII و وابسته به Flow هستند و پاسخ دیررس جست‌وجو یا قیمت پس از لغو، `/start` یا بازگشت به منوی اصلی نمی‌تواند FSM را زنده یا سیگنال را ثبت کند. Runtime از `MarketProbe` و Adapterهای موجود برای Catalog و Reference Price استفاده می‌کند و معماری HTTP صرافی دیگری ندارد.

مشخصات Canvas در `chainura/rendering/models.py` متمرکز است: Ourbit برابر 1080×1148، Toobit برابر 942×1010، LBank برابر 830×1005 و WEEX برابر 875x969، همگی PNG. چیدمان WEEX یک طرح declarative اولیه و عملیاتی است؛ Main Admin قالب پیش‌فرض/اختصاصی را مدیریت می‌کند و تنظیم نهایی مختصات و تایپوگرافی فقط از Renderer Lab انجام می‌شود.

## Phase 5C initial publication

A confirmed Signal now creates a SQLite-backed publication outbox atomically and returns immediately to the Signalist. The background worker plans compatible active assigned destinations from persisted source snapshots, enforces Asia/Tehran daily initial-message slots, renders destination templates, sends Persian HTML photo captions, and stores Telegram message IDs. Private initial captions include `#پیامک`; destination information is never disclosed to Signalists.

Templates are immutable, content-addressed PNG documents installed as exchange defaults or destination overrides by the Main Admin workflow. Missing templates remain retryable. Definite failures release capacity; ambiguous sends become `delivery_unknown`, retain capacity, and are not automatically resent. Generated initial images are cleaned after use and owned-pattern orphans are cleaned before worker startup. Monitoring and approved update publication remain Phase 6 work.
## Phase 6A monitoring

Open Signals are durably enrolled in SQLite and evaluated from one batched,
coherent market snapshot. TP events are traversed in order, SL is terminal only
before TP1, and exact event-time exchange prices are retained separately from the
configured ROI threshold. Active Signalists receive durable direct notifications
and may approve or decline future destination publication. Phase 6A records that
intent only; destination rendering, publication, retries, and Reply Chains remain
Phase 6B work.

Current Signals do not create automatic Breakeven prompts. After an eligible TP,
the Signalist may explicitly activate Risk Free, which moves Effective Stop to
Entry. A later Entry hit records `RISK_FREE_HIT` and closes the remaining slice at
exact zero ROI. Historical Breakeven events, prompts, routes, and report rows remain
readable compatibility data only.

### Phase 6B update publication

Approved current TP/SL/Risk-Free lifecycle updates, plus historical Breakeven candidates retained for compatibility, use durable update-publication requests. The worker waits for completed initial publication, plans only destinations with confirmed initial messages, renders a new event-time image with the existing template hierarchy, sends a concise Persian caption as a Telegram photo reply, preserves per-destination Reply Chains, and recovers safely after restart. Updates do not consume initial daily capacity and never include `#پیامک`.

## Phase 6C reports and API health

Main Admin reports are on-demand only. Report dates use the Asia/Tehran calendar and are converted to half-open UTC intervals based on Signal creation time. Win Rate is `wins / (wins + losses) * 100`; historical Breakeven and active no-result Signals are excluded. Theoretical ROI uses persisted event ROI: highest reached TP for wins, SL ROI for losses, zero for historical Breakeven, and never current market prices.

The API-health subsystem durably monitors eight services: independent price and catalog services for Ourbit, Toobit, LBank, and WEEX. WEEX catalog failure does not disable the three-exchange Shared core and missing optional catalog recovery is autonomous. `down` opens one outage incident per service and sends immediate Main Admin alert, one repeat every 60 seconds while still down, and one recovery alert after usable connectivity returns. `degraded` is shown in health status but does not open an incident. The bot does not reconstruct historical events missed during outages.

### Phase 4B: اقدام‌های پوزیشن

پس از هر تارگت تأییدشده، سیگنالیست اقدام عادی، ریسک فری، سیو سود ۵۰٪ یا بستن کامل
را انتخاب می‌کند (در تارگت نهایی فقط عادی و بستن کامل). ریسک فری و سیو سود مستقل
و هرکدام یک‌بار مصرف‌اند. حسابداری Decimal وزن‌دار Phase 4A منبع حقیقت است؛ حد ضرر
پس از TP1 نیز تا زمانی که ریسک فری فعال نشده پایش می‌شود و ریسک فری، Stop مؤثر را
دقیقاً به Entry منتقل می‌کند. جریان قدیمی Prompt بریک‌ایون دیگر دادهٔ جدید نمی‌سازد.
انتشار متن اقدام فقط پس از `SENT` شدن تصویر TP و به‌صورت Reply به همان پیام انجام
می‌شود؛ نتیجهٔ مبهم ارسال هرگز بازفرست یا حدس زده نمی‌شود.

## Market and Limit entry lifecycle

Chainura distinguishes durable `MARKET` and `LIMIT` entry types. Existing records
are migrated explicitly to Market. Market creation and its drift protection remain
unchanged. A Limit is created only after all levels have been supplied and a fresh
snapshot proves that a LONG entry is below, or a SHORT entry is above, the current
reference price. Its requested entry remains authoritative throughout its life.

A Limit has no expiration and follows `PENDING_ENTRY → ENTRY_TRIGGERED → ACTIVE`.
The trigger is irreversible, durable, and uses Chainura's sampled reference prices
from the shared batched market cache—not candle wicks or reconstructed trade
history. Reaching entry never activates the Signal: its Signalist must explicitly
activate it. Before activation, TP/SL and position accounting do not run.

Pending Limit text is the destination admission message. Admission retains the
same global `(destination, canonical symbol)` Trade Lock and consumes one Tehran
local-day initial slot; activation consumes neither a second slot nor a second
lock. A destination blocked at admission is never backfilled. The activated photo
must reply to the definitely-sent pending message, so an unknown parent never
causes a standalone fallback.

Limit trigger, publication, capacity, and lock facts live in SQLite. Restart
recovery treats interrupted sends as `delivery_unknown`, never blindly resends an
ambiguous Telegram delivery, and resumes retryable work without automatic
activation. Telegram rate limits are stored as `retry_wait` using the explicit
Telegram delay, while permanent rejections use privacy-safe categories and do not
persist raw exception text.

### Terminal trade patch review

See [the targeted lifecycle runbook](docs/TERMINAL_TRADE_PATCH.md) for reporting
weighting, independent Full Close delivery, conservative automatic reconciliation,
audit logs, and the history-preserving downgrade guard in migration `20261026_01`.
