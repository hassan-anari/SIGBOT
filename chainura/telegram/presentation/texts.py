from __future__ import annotations
from html import escape
from datetime import timezone
from chainura.telegram.presentation.formatting import (EXCHANGE_NAMES, RLM, bidi_ascii_id,
    format_datetime, format_priority, persian_digits)

MENU_MAIN="🏠 منوی اصلی"; MENU_ADMIN_STATUS="🛡 وضعیت دسترسی"; MENU_SIGNALIST_STATUS="👤 وضعیت حساب"
MENU_HELP="❓ راهنما"; MENU_ADMIN_SIGNALISTS="👥 مدیریت سیگنالیست‌ها"; MENU_ADMIN_DESTINATIONS="📡 مدیریت مقصدها"; MENU_ADMIN_TEMPLATES="🖼 مدیریت قالب‌ها"
MENU_CREATE_SIGNAL="➕ ساخت سیگنال"
BUTTON_BACK="⬅️ بازگشت به منوی اصلی"; BUTTON_REFRESH="تازه‌سازی وضعیت"
WELCOME_ADMIN="<b>👋 خوش آمدید</b>\n\nشما مدیر اصلی سامانه هستید."
WELCOME_SIGNALIST="<b>👋 خوش آمدید، {name}</b>\n\nحساب سیگنالیست شما فعال است."
DASHBOARD_ADMIN="<b>🏠 منوی اصلی مدیر</b>\n\nیکی از گزینه‌ها را انتخاب کنید."
DASHBOARD_SIGNALIST="<b>🏠 منوی اصلی سیگنالیست</b>\n\nیکی از گزینه‌ها را انتخاب کنید."
HELP_ADMIN="<b>❓ راهنمای مدیر</b>\n\nمدیریت سیگنالیست‌ها، مقصدها، اولویت صرافی و انتساب‌ها در دسترس است."
HELP_SIGNALIST="<b>❓ راهنمای سیگنالیست</b>\n\nاز منوی اصلی می‌توانید ساخت سیگنال را آغاز کنید."
ASK_SHARED_SYMBOL="<b>🔎 ساخت سیگنال</b>\n\nنماد موردنظر را برای جست‌وجو در فهرست مشترک وارد کنید."
SIGNAL_RECORDED_SUCCESS="✅ سیگنال با موفقیت ثبت شد."
REFERENCE_UNAVAILABLE="⚠️ قیمت مرجع سالم و تازه در دسترس نیست؛ کمی بعد دوباره تلاش کنید."
ENTRY_OUT_OF_TOLERANCE="❌ اختلاف قیمت ورود با قیمت مرجع بیش از ۰٫۰۵٪ است."
STATUS_ADMIN="<b>🛡 وضعیت دسترسی</b>\n\nنقش: مدیر اصلی\nوضعیت: 🟢 فعال"
STATUS_SIGNALIST="<b>👤 وضعیت حساب</b>\n\nنام: {name}\nنقش: سیگنالیست\nوضعیت: 🟢 فعال"
INACTIVE="حساب شما غیرفعال است و امکان انجام عملیات جدید را ندارد."; ARCHIVED="حساب شما بایگانی شده است."
UNKNOWN="دسترسی شما هنوز در سامانه تعریف نشده است."; FALLBACK="دستور شناخته نشد؛ از منو استفاده کنید."
INVALID_CALLBACK="این دکمه معتبر نیست یا دیگر قابل استفاده نیست."; DUPLICATE_CALLBACK="این درخواست قبلاً دریافت شده است."
TEMPORARY_ERROR="در حال حاضر انجام درخواست ممکن نیست. کمی بعد دوباره تلاش کنید."
COMMAND_START="شروع و نمایش منوی اصلی"; COMMAND_HELP="نمایش راهنما"
BUTTON_ADD="➕ افزودن"; BUTTON_CANCEL="❌ لغو"; BUTTON_CONFIRM="✅ تأیید"; BUTTON_PREVIOUS="قبلی"; BUTTON_NEXT="بعدی"
BUTTON_ALL="همه"; BUTTON_ACTIVE="فعال"; BUTTON_INACTIVE="غیرفعال"; BUTTON_ARCHIVED="بایگانی‌شده"
BUTTON_EDIT="✏️ ویرایش"; BUTTON_PRIORITY="اولویت صرافی‌ها"; BUTTON_ASSIGNMENTS="سیگنالیست‌های مقصد"
BUTTON_ACTIVATE="🟢 فعال‌سازی"; BUTTON_DEACTIVATE="🟡 غیرفعال‌سازی"; BUTTON_ARCHIVE="🗃 بایگانی"
BUTTON_BACK_STEP="⬅️ بازگشت"; BUTTON_ASSIGNED_DESTINATIONS="مقصدهای اختصاص‌یافته"
BUTTON_CONFIRM_DESTINATION="✅ تأیید مقصد"; BUTTON_REENTER_CHAT="🔄 واردکردن شناسه دیگر"
BUTTON_UP="▲"; BUTTON_DOWN="▼"
SIGNALIST_LIST_TITLE="<b>👥 مدیریت سیگنالیست‌ها</b>\n\n📄 صفحه {page} از {pages}\nفیلتر: {filter}\n\nیکی از سیگنالیست‌ها را انتخاب کنید."
DESTINATION_LIST_TITLE="<b>📡 مدیریت مقصدها</b>\n\n📄 صفحه {page} از {pages}\nفیلتر: {filter}\n\nیکی از مقصدها را انتخاب کنید."
EMPTY_LIST="موردی برای نمایش وجود ندارد."; ASK_SIGNALIST_ID=RLM+"🆔 شناسه عددی تلگرام سیگنالیست را وارد کنید."
ASK_SIGNALIST_NAME="📝 نام نمایشی سیگنالیست را وارد کنید."
ASK_DESTINATION_CHAT_ID=RLM+"🆔 شناسه چت کانال یا گروه را وارد کنید."
ASK_DAILY_LIMIT="📊 سقف سیگنال روزانه را به‌صورت عدد مثبت وارد کنید."
INVALID_INPUT="مقدار واردشده معتبر نیست؛ دوباره تلاش کنید."; INVALID_CHAT_ID="❌ شناسه چت معتبر نیست.\n\nشناسه را بررسی کنید و دوباره تلاش کنید."
STALE_ACTION="اطلاعات تغییر کرده است؛ نمای فعلی تازه‌سازی شد."
DESTINATION_NOT_FOUND="❌ مقصد پیدا نشد یا ربات به آن دسترسی ندارد.\n\nلطفاً بررسی کنید ربات عضو کانال یا گروه باشد و دسترسی مدیر داشته باشد، سپس دوباره تلاش کنید."
DESTINATION_PRIVATE="❌ این شناسه مربوط به گفت‌وگوی خصوصی است.\n\nفقط کانال یا گروه قابل ثبت است."
DESTINATION_NOT_ADMIN="⚠️ ربات در این مقصد مدیر نیست.\n\nابتدا ربات را به‌عنوان مدیر کانال یا گروه اضافه کنید و سپس دوباره تلاش کنید."
TELEGRAM_TEMPORARY="⚠️ ارتباط با تلگرام موقتاً برقرار نشد.\n\nچند لحظه بعد دوباره تلاش کنید."
CANDIDATE_CHANGED="⚠️ اطلاعات مقصد در تلگرام تغییر کرده است.\n\nاطلاعات تازه را دوباره تأیید کنید."
FLOW_CANCELLED="فرایند لغو شد."; FLOW_EXPIRED="این فرایند منقضی شده است؛ دوباره آغاز کنید."
CREATED_SUCCESS="✅ اطلاعات با موفقیت ثبت شد."; DUPLICATE_SIGNALIST="این شناسه تلگرام قبلاً ثبت شده است."
DUPLICATE_DESTINATION_NAME="مقصدی با این نام تلگرام قبلاً ثبت شده است."; DUPLICATE_DESTINATION_CHAT="این شناسه چت قبلاً ثبت شده است."
ASSIGNMENT_DUPLICATE="این سیگنالیست قبلاً به مقصد اختصاص یافته است."; ASSIGNMENT_MISSING="این انتساب قبلاً حذف شده است."
ASSIGNMENT_INELIGIBLE="سیگنالیست بایگانی‌شده قابل انتساب نیست."; ASK_DESTINATION_ACCESS="سطح دسترسی مقصد را انتخاب کنید."
ASK_PRIORITY="🏦 اولویت صرافی‌ها را تنظیم کنید."
EDIT_PRIORITY="🏦 اولویت صرافی‌ها را ویرایش کنید."; EMPTY_VALUE="خالی"; TYPE_CHANNEL="📢 کانال"; TYPE_GROUP="👥 گروه"
ACCESS_PUBLIC="🌐 عمومی"; ACCESS_PRIVATE="🔒 خصوصی"; SHOW_ELIGIBLE="نمایش واجدان شرایط"; SHOW_ASSIGNED="نمایش انتساب‌های فعلی"
CONFIRM_UNASSIGN="آیا از حذف این انتساب مطمئن هستید؟"; CONFIRM_METADATA="تغییر مشخصات تأیید شود؟"
CONFIRM_ARCHIVE_BUTTON="🗃 تأیید بایگانی"; ASSIGNED_DESTINATIONS_TITLE="<b>مقصدهای اختصاص‌یافته</b>\nصفحه {page} از {pages}"
ADD_EXCHANGE="➕ افزودن {exchange}"; STATUS_LABELS={"active":"🟢 فعال","inactive":"🟡 غیرفعال","archived":"🔴 بایگانی‌شده"}
TYPE_LABELS={"channel":"📢 کانال","group":"👥 گروه"}; ACCESS_LABELS={"public":"🌐 عمومی","private":"🔒 خصوصی"}
NOT_RECORDED="ثبت نشده"
DATETIME_TEMPLATE="{date}، ساعت {time}"
PRIORITY_HEADING="🏦 اولویت صرافی‌ها:"
SIGNALIST_FILTER_LABELS={"a":"همه","v":"🟢 فعال","i":"🟡 غیرفعال","r":"🔴 بایگانی‌شده"}
DESTINATION_FILTER_LABELS={"a":"همه","v":"🟢 فعال","i":"🟡 غیرفعال"}
TEMPLATE_MENU="<b>🖼 مدیریت قالب‌ها</b>\n\nصرافی را انتخاب کنید."
TEMPLATE_DEFAULT_STATUS="قالب پیش‌فرض {exchange}: {status}"
TEMPLATE_OVERRIDE_STATUS="قالب اختصاصی مقصد «{destination}» برای {exchange}: {status}"
TEMPLATE_CONFIGURED="✅ تنظیم شده"; TEMPLATE_MISSING="تنظیم نشده"
TEMPLATE_UPLOAD_DEFAULT="بارگذاری/جایگزینی پیش‌فرض"; TEMPLATE_SELECT_DESTINATION="انتخاب مقصد"
TEMPLATE_UPLOAD_OVERRIDE="بارگذاری/جایگزینی اختصاصی"; TEMPLATE_REMOVE_OVERRIDE="حذف قالب اختصاصی"
TEMPLATE_SEND_DOCUMENT="فایل PNG را به‌صورت سند ارسال کنید؛ ارسال به‌صورت عکس پذیرفته نیست."
TEMPLATE_DOCUMENT_REQUIRED="فقط سند PNG معتبر پذیرفته می‌شود."
TEMPLATE_INSTALLED="✅ قالب با موفقیت نصب شد."; TEMPLATE_REMOVED="✅ قالب اختصاصی حذف شد و قالب پیش‌فرض استفاده می‌شود."
TEMPLATE_REMOVE_CONFIRM="آیا حذف قالب اختصاصی این مقصد را تأیید می‌کنید؟"
TEMPLATE_ALL="همه"

def safe(value): return escape(str(value),quote=True)
def welcome_signalist(name): return WELCOME_SIGNALIST.format(name=safe(name))
def status_signalist(name): return STATUS_SIGNALIST.format(name=safe(name))
def page_title(template,page,pages,filter_label="همه"): return template.format(page=persian_digits(page),pages=persian_digits(pages),filter=filter_label)
def signalist_line(item): return f"{STATUS_LABELS[item.status.value].split()[0]} {safe(item.display_name)}"
def destination_line(item): return f"{STATUS_LABELS[item.status.value].split()[0]} {safe(item.internal_name)}"
def signalist_button_label(item): return f"{STATUS_LABELS[item.status.value].split()[0]} {item.display_name}"
def destination_button_label(item): return f"{STATUS_LABELS[item.status.value].split()[0]} {item.internal_name}"
def datetime_value(value): return format_datetime(value, DATETIME_TEMPLATE) or NOT_RECORDED
def priority_value(values): return format_priority(values, PRIORITY_HEADING, NOT_RECORDED)
def priority_current(values): return priority_value(values)
def telegram_id_block(label, value): return f"{RLM}🆔 {label}:\n{bidi_ascii_id(value)}"
def signalist_review(data, username=None):
    return f"<b>👤 بررسی اطلاعات سیگنالیست</b>\n\n{telegram_id_block('شناسه تلگرام', data['telegram_user_id'])}\n\n📝 نام نمایشی: {safe(data['display_name'])}\n\nآیا اطلاعات بالا تأیید می‌شود؟"
def signalist_detail(item,count):
    value=f"<b>👤 {safe(item.display_name)}</b>\n\n{STATUS_LABELS[item.status.value].split()[0]} وضعیت: {STATUS_LABELS[item.status.value].split(maxsplit=1)[1]}\n{telegram_id_block('شناسه تلگرام', item.telegram_user_id)}\n\n📍 مقصدهای متصل: {persian_digits(count)}\n\n📅 ایجاد: {datetime_value(item.created_at)}\n🔄 آخرین تغییر: {datetime_value(item.updated_at)}"
    return value+(f"\n🗃 بایگانی: {datetime_value(item.archived_at)}" if item.archived_at else "")
def destination_detail(detail):
    d=detail.destination
    return f"<b>📡 {safe(d.internal_name)}</b>\n\n{telegram_id_block('شناسه چت', d.telegram_chat_id)}\n\n{TYPE_LABELS[d.destination_type.value].split()[0]} نوع: {TYPE_LABELS[d.destination_type.value].split(maxsplit=1)[1]}\n{ACCESS_LABELS[d.access_level.value].split()[0]} دسترسی: {ACCESS_LABELS[d.access_level.value].split(maxsplit=1)[1]}\n{STATUS_LABELS[d.status.value].split()[0]} وضعیت: {STATUS_LABELS[d.status.value].split(maxsplit=1)[1]}\n📊 سقف سیگنال روزانه: {persian_digits(d.daily_limit)}\n👥 سیگنالیست‌های متصل: {persian_digits(detail.assigned_count)}\n\n{priority_value(detail.priority.exchanges)}\n\n📅 ایجاد: {datetime_value(d.created_at)}\n🔄 آخرین تغییر: {datetime_value(d.updated_at)}"
def candidate_review(data):
    return f"<b>✅ مقصد پیدا شد</b>\n\n📡 نام: {safe(data['internal_name'])}\n{TYPE_LABELS[data['destination_type']].split()[0]} نوع: {TYPE_LABELS[data['destination_type']].split(maxsplit=1)[1]}\n\n{telegram_id_block('شناسه چت', data['telegram_chat_id'])}\n\nآیا این همان مقصد موردنظر است؟"
def destination_review(data):
    return f"<b>📋 بررسی نهایی مقصد</b>\n\n📡 نام: {safe(data['internal_name'])}\n\n{telegram_id_block('شناسه چت', data['telegram_chat_id'])}\n\n{TYPE_LABELS[data['destination_type']].split()[0]} نوع: {TYPE_LABELS[data['destination_type']].split(maxsplit=1)[1]}\n{ACCESS_LABELS[data['access_level']].split()[0]} دسترسی: {ACCESS_LABELS[data['access_level']].split(maxsplit=1)[1]}\n🟢 وضعیت اولیه: فعال\n📊 سقف سیگنال روزانه: {persian_digits(data['daily_limit'])}\n\n{priority_value(data['exchanges'])}\n\nآیا اطلاعات بالا ثبت شود؟"
def metadata_review(data): return f"<b>📋 بررسی ویرایش مقصد</b>\n\n{ACCESS_LABELS[data['access_level']].split()[0]} دسترسی: {ACCESS_LABELS[data['access_level']].split(maxsplit=1)[1]}\n📊 سقف سیگنال روزانه: {persian_digits(data['daily_limit'])}\n\nتغییرات تأیید شود؟"
def archive_confirmation(name): return f"<b>⚠️ بایگانی سیگنالیست</b>\n\nاین عملیات برگشت‌پذیر نیست.\n\nآیا از بایگانی «{safe(name)}» مطمئن هستید؟"

# Phase 5A signal creation
SIGNAL_SEARCH_OUTSIDE="🔎 جست‌وجو خارج از فهرست مشترک"
SIGNAL_SELECT_EXCHANGE="صرافی قرارداد را انتخاب کنید."
SIGNAL_ASK_NATIVE="نماد یا نام دارایی را برای جست‌وجوی قرارداد وارد کنید."
SIGNAL_EMPTY_SEARCH="قرارداد منطبقی پیدا نشد؛ عبارت دیگری وارد کنید."
SIGNAL_SELECT_RESULT="قرارداد دقیق را انتخاب کنید."
SIGNAL_SELECT_DIRECTION="جهت معامله را انتخاب کنید."
SIGNAL_LONG="🟢 لانگ"; SIGNAL_SHORT="🔴 شورت"
SIGNAL_ASK_ENTRY="قیمت ورود را با ارقام انگلیسی وارد کنید."
SIGNAL_ENTRY_VALID="✅ قیمت ورود معتبر است."
SIGNAL_ASK_LEVERAGE="اهرم را به‌صورت عدد صحیح مثبت انگلیسی وارد کنید."
SIGNAL_ASK_TP="قیمت تارگت را با ارقام انگلیسی وارد کنید."
SIGNAL_TP_ADD="➕ افزودن تارگت بعدی"; SIGNAL_TP_FINISH="✅ پایان ثبت تارگت‌ها"
SIGNAL_ASK_SL="قیمت حد ضرر را با ارقام انگلیسی وارد کنید."
SIGNAL_INVALID_DIRECTIONAL_PRICE="قیمت با جهت، ترتیب تارگت‌ها یا قیمت ورود سازگار نیست."
SIGNAL_REVIEW_TITLE="<b>📋 بررسی نهایی سیگنال</b>"
SIGNAL_CONFIRM="✅ تأیید و ثبت"
SIGNAL_CANCEL="❌ لغو"
SIGNAL_BACK="⬅️ بازگشت"
SIGNAL_BACK_EXCHANGE="⬅️ بازگشت به انتخاب صرافی"
SIGNAL_BACK_SHARED="↩️ بازگشت به جست‌وجوی مشترک"
SIGNAL_STALE="این دکمه مربوط به فرایند قبلی است."
SIGNAL_PROVIDER_FAILURE="⚠️ دریافت اطلاعات بازار ممکن نیست؛ کمی بعد دوباره تلاش کنید."
FINAL_ENTRY_OUT_OF_TOLERANCE="❌ قیمت ورود دیگر در محدوده مجاز ۰٫۰۵٪ قیمت مرجع نیست."
SIGNAL_FINALIZING="⏳ ثبت نهایی سیگنال در حال انجام است؛ لطفاً منتظر بمانید."
SIGNALIST_NO_LONGER_ACTIVE="حساب شما دیگر فعال نیست و ثبت سیگنال انجام نشد."
SIGNAL_FINISH_TP_BLOCKED="ابتدا دست‌کم یک تارگت معتبر ثبت کنید."
SIGNAL_MODE_COMPOSITE="مشترک"; SIGNAL_MODE_SINGLE="تک‌صرافی"
SIGNAL_DIRECTION_LONG="لانگ"; SIGNAL_DIRECTION_SHORT="شورت"
SIGNAL_PAGE="صفحه {page} از {pages}"
SIGNAL_TPS_HEADING="تارگت‌های ثبت‌شده:"
PRICE_SOURCE_LABELS={
    "composite":"مرجع مشترک",
    "ourbit_only":"فقط Ourbit",
    "toobit_only":"فقط Toobit",
    "lbank_only":"فقط LBank",
}

def signal_search_title(page, pages):
    return SIGNAL_SELECT_RESULT+"\n"+SIGNAL_PAGE.format(page=persian_digits(page),pages=persian_digits(pages))

def signal_tp_list(values):
    from chainura.telegram.presentation.formatting import format_ordered_take_profits
    return SIGNAL_TPS_HEADING+"\n"+format_ordered_take_profits(values)

def signal_review(data):
    from decimal import Decimal
    from chainura.telegram.presentation.formatting import format_decimal, format_leverage, format_symbol
    direction=SIGNAL_DIRECTION_LONG if data["direction"]=="long" else SIGNAL_DIRECTION_SHORT
    keycaps=("1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣")
    targets="\n".join(
        f"{keycaps[index - 1] if index <= len(keycaps) else f'TP{index}'} {format_decimal(Decimal(value))}"
        for index,value in enumerate(data["take_profits"],1)
    )
    rows=[
        SIGNAL_REVIEW_TITLE,
        "",
        f"🪙 <b>نماد:</b> {format_symbol(data['canonical_symbol'])}",
        f"{'📈' if data['direction']=='long' else '📉'} <b>جهت:</b> {direction}",
        f"💵 <b>قیمت ورود:</b> {format_decimal(Decimal(data['entry_price']))}",
        f"⚡ <b>اهرم:</b> {format_leverage(data['leverage'])}",
        "",
        "🎯 <b>تارگت‌ها:</b>",
        targets,
        "",
        f"🛑 <b>حد ضرر:</b> {format_decimal(Decimal(data['stop_loss']))}",
    ]
    return "\n".join(rows)
SIGNAL_PRICE_UNAVAILABLE = "⚠️ قیمت فعلی این نماد ناموجود است. لطفاً دوباره تلاش کنید."
EVENT_APPROVED = "✅ درخواست انتشار این آپدیت ثبت شد."
EVENT_DECLINED = "این آپدیت برای مقصدها منتشر نخواهد شد."
EVENT_DECISION_STALE = "⚠️ این درخواست دیگر قابل انجام نیست."

# Phase 6A event and Breakeven actions
EVENT_PUBLISH_BUTTON="✅ ارسال به مقصدها"
EVENT_DECLINE_BUTTON="❌ عدم انتشار"
BREAKEVEN_ACCEPT_BUTTON="✅ ثبت بریک‌ایون"
BREAKEVEN_DECLINE_BUTTON="❌ فعلاً نه"
BREAKEVEN_DECLINED="این درخواست بریک‌ایون ثبت نشد."
BREAKEVEN_REGISTERED="✅ بریک‌ایون ثبت شد. آپدیت رویداد برای تصمیم انتشار نمایش داده خواهد شد."
BREAKEVEN_OUT_OF_RANGE="⚠️ قیمت در حال حاضر خارج از محدوده بریک‌ایون است.\nبریک‌ایون ثبت نشد."
BREAKEVEN_STALE="این درخواست بریک‌ایون دیگر قابل استفاده نیست."

# Phase 6C reports and API health
MENU_ADMIN_REPORTS="📊 گزارش‌ها"
REPORT_SCOPE_TITLE="<b>📊 گزارش‌ها</b>\n\nدامنه گزارش را انتخاب کنید."
REPORT_ALL_SIGNALISTS="همه سیگنالیست‌ها"
REPORT_ONE_SIGNALIST="یک سیگنالیست"
REPORT_PERIOD_TITLE="بازه گزارش را انتخاب کنید."
REPORT_TODAY="امروز"; REPORT_7D="۷ روز اخیر"; REPORT_30D="۳۰ روز اخیر"; REPORT_CUSTOM="بازه دلخواه"
REPORT_ASK_START="تاریخ شروع را به میلادی و با قالب YYYY-MM-DD وارد کنید."
REPORT_ASK_END="تاریخ پایان را به میلادی و با قالب YYYY-MM-DD وارد کنید."
REPORT_INVALID_RANGE="بازه تاریخ معتبر نیست؛ تاریخ‌ها را با قالب YYYY-MM-DD و به‌ترتیب درست وارد کنید."
REPORT_HISTORY="تاریخچه معاملات"; REPORT_REFRESH="تازه‌سازی"; REPORT_YES="بله"; REPORT_NO="خیر"; REPORT_DASH="—"
HEALTH_STATUS_TITLE="<b>🛡 وضعیت APIها</b>"
HEALTH_LABELS={"ourbit_prices":"قیمت Ourbit","ourbit_catalog":"فهرست قراردادهای Ourbit","toobit_prices":"قیمت Toobit","toobit_catalog":"فهرست قراردادهای Toobit","lbank_prices":"قیمت LBank","lbank_catalog":"فهرست قراردادهای LBank"}
HEALTH_STATUS_LABELS={"unknown":"نامشخص","healthy":"سالم","degraded":"محدود","down":"قطع"}
VALID_PRICE_UNAVAILABLE="در حال حاضر دریافت قیمت معتبر امکان‌پذیر نیست.\nکمی بعد دوباره تلاش کنید."
def _pct(value): return REPORT_DASH if value is None else persian_digits(f"{value.quantize(__import__('decimal').Decimal('0.01'))}%")
def _dec(value): return REPORT_DASH if value is None else persian_digits(str(value.quantize(__import__('decimal').Decimal('0.01'))))
def report_summary_text(summary, scope_label=None, start_text=None, end_text=None):
    rows=["<b>📊 خلاصه گزارش</b>"]
    if scope_label: rows.append(f"دامنه: {safe(scope_label)}")
    if start_text and end_text: rows.append(f"بازه: {safe(start_text)} تا {safe(end_text)}")
    rows.extend(["",f"کل سیگنال‌ها: {persian_digits(summary.total_signals)}",f"سیگنال‌های فعال: {persian_digits(summary.active_signals)}",f"دارای نتیجه: {persian_digits(summary.resulted_signals)}",f"برد: {persian_digits(summary.wins)}",f"باخت: {persian_digits(summary.losses)}",f"بریک‌ایون: {persian_digits(summary.breakevens)}",f"وین‌ریت: {_pct(summary.win_rate)}",f"ROI کل نظری: {_dec(summary.total_roi)}",f"میانگین ROI نظری: {_dec(summary.average_roi)}",f"میانگین ROI مثبت: {_dec(summary.average_positive_roi)}",f"بیشترین ROI مثبت: {_dec(summary.maximum_positive_roi)}",f"میانگین ROI منفی: {_dec(summary.average_negative_roi)}",f"کمترین ROI: {_dec(summary.minimum_negative_roi)}",f"توزیع TP: TP1={persian_digits(summary.tp1_count)}، TP2={persian_digits(summary.tp2_count)}، TP3+={persian_digits(summary.tp3_plus_count)}",f"مرجع مشترک: {persian_digits(summary.composite_count)}",f"تک‌صرافی: {persian_digits(summary.single_exchange_count)}",f"منتشرشده قطعی: {persian_digits(summary.confirmed_published_count)}",f"منتشرنشده: {persian_digits(summary.unpublished_count)}"])
    return "\n".join(rows)
def _report_direction(value): return {"long":"لانگ","short":"شورت"}.get(str(value), str(value))
def _report_source(value): return {"Composite":"مرجع مشترک","Single Exchange":"تک‌صرافی"}.get(str(value), str(value))
def _report_state(value):
    text=str(value)
    if text.startswith("Active Win"): return text.replace("Active Win at TP", "برد فعال در TP")
    if text.startswith("Completed Win"): return text.replace("Completed Win at TP", "برد تکمیل‌شده در TP")
    return {"Active without result":"فعال بدون نتیجه","Loss":"باخت","Breakeven":"بریک‌ایون"}.get(text,text)
def trade_history_text(page, all_scope=True):
    rows=[f"<b>📜 تاریخچه معاملات</b>\nصفحه {persian_digits(page.page)} از {persian_digits(page.pages)}"]
    for item in page.items:
        who=f"\nسیگنالیست: {safe(item.signalist_display_name)}" if all_scope else ""
        rows.append(f"\n#{persian_digits(item.signal_id)}{who}\nنماد: {safe(item.canonical_symbol)}\nجهت: {safe(_report_direction(item.direction))}\nزمان: {datetime_value(item.created_at_tehran)}\nمنبع: {safe(_report_source(item.source_label))}\nوضعیت: {safe(_report_state(item.state))}\nبالاترین TP: {persian_digits(item.highest_tp) if item.highest_tp else REPORT_DASH}\nROI: {_dec(item.theoretical_roi)}\nانتشار قطعی: {REPORT_YES if item.confirmed_published else REPORT_NO}")
    if not page.items: rows.append(EMPTY_LIST)
    return "\n".join(rows)
def _duration_text(start, end):
    if start is None or end is None:
        return REPORT_DASH
    seconds = max(0, int((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return persian_digits(f"{hours} ساعت و {minutes} دقیقه")
    if minutes:
        return persian_digits(f"{minutes} دقیقه")
    return persian_digits(f"{sec} ثانیه")

def health_status_text(rows, now=None):
    out=[HEALTH_STATUS_TITLE]
    for row in rows:
        last=datetime_value(row.last_success_at) if row.last_success_at else REPORT_DASH
        extra=""
        if getattr(row,'status',None) and row.status.value=='down' and row.unhealthy_since:
            extra=f"\nشروع اختلال: {datetime_value(row.unhealthy_since)}"
            if now is not None:
                extra += f"\nمدت فعلی: {_duration_text(row.unhealthy_since, now)}"
        out.append(f"\n{HEALTH_LABELS.get(row.service_key,row.service_key)}\nوضعیت: {HEALTH_STATUS_LABELS.get(row.status.value,row.status.value)}\nآخرین موفقیت: {last}{extra}")
    return "\n".join(out)
