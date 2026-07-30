# Haz Market Data — REST Probe + File Logging

این پروژه یک Probe ساده برای بررسی عملی بازارهای Futures/Perpetual سه صرافی است. عمداً دیتابیس، Redis، Docker و معماری پیچیده ندارد.

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
- ثبت دائمی خطاها و وضعیت اجرا در فایل چرخشی `logs/market.log`

## وابستگی

هیچ Dependency خارجی وجود ندارد. Python 3.11 یا جدیدتر پیشنهاد می‌شود.

```powershell
python main.py symbols
python main.py watch --seconds 30
```

تست‌ها:

```powershell
python -m unittest discover -s tests -v
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
- `lbank_ignored_numeric_symbols.json`: Numeric Symbolهای حذف‌شده LBank

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
