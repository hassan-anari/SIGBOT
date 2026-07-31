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

## وابستگی

Python 3.11 یا جدیدتر و برای بخش پایدار برنامه PostgreSQL لازم است. وابستگی‌ها:

```bash
python -m pip install -r requirements.txt
```

```powershell
python main.py symbols
python main.py watch --seconds 30
```

تست‌ها:

```powershell
python -m unittest discover -s tests -v
```

این تست‌های سریع آفلاین هستند و به PostgreSQL یا API زندهٔ صرافی متصل نمی‌شوند.

## پایهٔ پایدار برنامه

تنها موجودیت پایدار فعلی `Signalist` است. وضعیت‌های مجاز آن `active`،
`inactive` و `archived` هستند و Archive به‌صورت Soft Delete انجام می‌شود. Main
Admin جدول دیتابیس ندارد و شناسهٔ آن از محیط خوانده می‌شود.

دو متغیر برنامه لازم است:

```text
DATABASE_URL=postgresql+psycopg://app_user:password@localhost/chainura
MAIN_ADMIN_TELEGRAM_ID=123456789
```

فایل `.env.example` فقط نمونه است؛ برنامه فایل `.env` را خودکار بارگذاری نمی‌کند.
مقادیر واقعی را در Environment سیستم قرار دهید و URL کامل را در لاگ ننویسید.

برای توسعه یک دیتابیس محلی بسازید و Migration را اجرا کنید:

```bash
createdb chainura
export DATABASE_URL='postgresql+psycopg://app_user:password@localhost/chainura'
alembic upgrade head
alembic current
alembic history
```

Alembic تنها مکانیزم ساخت Schema است. برای بررسی Downgrade و Re-upgrade روی
دیتابیس disposable:

```bash
alembic downgrade base
alembic upgrade head
```

### تست PostgreSQL

تست‌های Integration مخرب‌اند و فقط دیتابیسی را می‌پذیرند که نام آن به `_test`
ختم شود. برای نمونه:

```bash
createdb chainura_phase2_test
export TEST_DATABASE_URL='postgresql+psycopg://test_user:test_password@localhost/chainura_phase2_test'
export DATABASE_URL="$TEST_DATABASE_URL"
python -m unittest discover -s integration_tests -v
```

نبودن یا ناامن بودن `TEST_DATABASE_URL` باعث شکست صریح Suite، پیش از هر تغییر
Schema، می‌شود.

### بررسی معادل CI در محیط محلی

```bash
python -m unittest discover -s tests -v
python -m compileall .
python main.py --help
python main.py symbols --help
python main.py watch --help
alembic upgrade head
alembic current
alembic downgrade base
alembic upgrade head
python -m unittest discover -s integration_tests -v
git diff --check
```

دستورهای `main.py` تنظیمات `DATABASE_URL`، `MAIN_ADMIN_TELEGRAM_ID` یا
`TEST_DATABASE_URL` را بارگذاری نمی‌کنند و Engine دیتابیس نمی‌سازند.


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
