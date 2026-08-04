from __future__ import annotations
from html import escape
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
    return f"<b>📋 بررسی نهایی مقصد</b>\n\n📡 نام: {safe(data['internal_name'])}\n\n{telegram_id_block('شناسه چت', data['telegram_chat_id'])}\n\n{TYPE_LABELS[data['destination_type']].split()[0]} نوع: {TYPE_LABELS[data['destination_type']].split(maxsplit=1)[1]}\n{ACCESS_LABELS[data['access_level']].split()[0]} دسترسی: {ACCESS_LABELS[data['access_level']].split(maxsplit=1)[1]}\n🟡 وضعیت اولیه: غیرفعال\n📊 سقف سیگنال روزانه: {persian_digits(data['daily_limit'])}\n\n{priority_value(data['exchanges'])}\n\nآیا اطلاعات بالا ثبت شود؟"
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
