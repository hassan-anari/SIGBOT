from __future__ import annotations

from html import escape

MENU_MAIN = "منوی اصلی"
MENU_ADMIN_STATUS = "وضعیت دسترسی"
MENU_SIGNALIST_STATUS = "وضعیت حساب"
MENU_HELP = "راهنما"
MENU_ADMIN_SIGNALISTS = "مدیریت سیگنالیست‌ها"
MENU_ADMIN_DESTINATIONS = "مدیریت مقصدها"
BUTTON_BACK = "بازگشت به منوی اصلی"
BUTTON_REFRESH = "تازه‌سازی وضعیت"

WELCOME_ADMIN = "<b>خوش آمدید.</b>\nشما مدیر اصلی سامانه هستید."
WELCOME_SIGNALIST = "<b>خوش آمدید، {name}.</b>\nحساب سیگنالیست شما فعال است."
DASHBOARD_ADMIN = "<b>منوی اصلی مدیر</b>\nیکی از گزینه‌های موجود را انتخاب کنید."
DASHBOARD_SIGNALIST = "<b>منوی اصلی سیگنالیست</b>\nیکی از گزینه‌های موجود را انتخاب کنید."
HELP_ADMIN = "<b>راهنمای مدیر</b>\nمدیریت سیگنالیست‌ها، مقصدها، اولویت صرافی و انتساب‌ها در دسترس است."
HELP_SIGNALIST = "<b>راهنمای سیگنالیست</b>\nدر این مرحله فقط منوی اصلی و وضعیت حساب در دسترس است."
STATUS_ADMIN = "<b>وضعیت دسترسی</b>\nنقش: مدیر اصلی\nوضعیت: فعال"
STATUS_SIGNALIST = "<b>وضعیت حساب</b>\nنام: {name}\nنقش: سیگنالیست\nوضعیت: فعال"
INACTIVE = "حساب شما غیرفعال است و امکان انجام عملیات جدید را ندارد."
ARCHIVED = "حساب شما بایگانی شده است و امکان دسترسی یا بازیابی آن از ربات وجود ندارد."
UNKNOWN = "دسترسی شما هنوز در سامانه تعریف نشده است."
FALLBACK = "دستور شناخته نشد؛ لطفاً از گزینه‌های منو استفاده کنید."
INVALID_CALLBACK = "این دکمه معتبر نیست یا دیگر قابل استفاده نیست."
DUPLICATE_CALLBACK = "این درخواست قبلاً دریافت شده است."
TEMPORARY_ERROR = "در حال حاضر انجام درخواست ممکن نیست. لطفاً کمی بعد دوباره تلاش کنید."
COMMAND_START = "شروع و نمایش منوی اصلی"
COMMAND_HELP = "نمایش راهنما"
BUTTON_ADD = "افزودن"
BUTTON_CANCEL = "لغو"
BUTTON_CONFIRM = "تأیید"
BUTTON_PREVIOUS = "قبلی"
BUTTON_NEXT = "بعدی"
BUTTON_ALL = "همه"
BUTTON_ACTIVE = "فعال"
BUTTON_INACTIVE = "غیرفعال"
BUTTON_ARCHIVED = "بایگانی‌شده"
BUTTON_EDIT = "ویرایش مشخصات"
BUTTON_PRIORITY = "اولویت صرافی‌ها"
BUTTON_ASSIGNMENTS = "سیگنالیست‌های مقصد"
BUTTON_ACTIVATE = "فعال‌سازی"
BUTTON_DEACTIVATE = "غیرفعال‌سازی"
BUTTON_ARCHIVE = "بایگانی"
BUTTON_BACK_STEP = "بازگشت"
BUTTON_ASSIGNED_DESTINATIONS = "مقصدهای اختصاص‌یافته"
SIGNALIST_LIST_TITLE = "<b>مدیریت سیگنالیست‌ها</b>\nصفحه {page} از {pages}"
DESTINATION_LIST_TITLE = "<b>مدیریت مقصدها</b>\nصفحه {page} از {pages}"
EMPTY_LIST = "موردی برای نمایش وجود ندارد."
ASK_SIGNALIST_ID = "شناسه عددی تلگرام سیگنالیست را وارد کنید."
ASK_SIGNALIST_NAME = "نام نمایشی سیگنالیست را وارد کنید."
ASK_SIGNALIST_USERNAME = "نام کاربری تلگرام را وارد کنید یا «رد کردن» را بفرستید."
ASK_DESTINATION_NAME = "نام داخلی مقصد را وارد کنید."
ASK_DESTINATION_CHAT_ID = "شناسه عددی چت مقصد را وارد کنید."
ASK_DAILY_LIMIT = "سقف روزانه پیام اولیه را به‌صورت عدد مثبت وارد کنید."
INVALID_INPUT = "مقدار واردشده معتبر نیست؛ دوباره تلاش کنید."
STALE_ACTION = "اطلاعات تغییر کرده است؛ نمای فعلی تازه‌سازی شد."
FLOW_CANCELLED = "فرایند لغو شد."
FLOW_EXPIRED = "این فرایند منقضی شده است؛ دوباره آغاز کنید."
CREATED_SUCCESS = "اطلاعات با موفقیت ثبت شد."
DUPLICATE_SIGNALIST = "این شناسه تلگرام قبلاً ثبت شده است."
DUPLICATE_DESTINATION_NAME = "این نام داخلی قبلاً ثبت شده است."
DUPLICATE_DESTINATION_CHAT = "این شناسه چت قبلاً ثبت شده است."
ASSIGNMENT_DUPLICATE = "این سیگنالیست قبلاً به مقصد اختصاص یافته است."
ASSIGNMENT_MISSING = "این انتساب قبلاً حذف شده است."
ASSIGNMENT_INELIGIBLE = "سیگنالیست بایگانی‌شده قابل انتساب نیست."
SKIP = "رد کردن"
ASK_DESTINATION_TYPE = "نوع مقصد را انتخاب کنید."
ASK_DESTINATION_ACCESS = "سطح دسترسی را انتخاب کنید."
ASK_DESTINATION_TYPE_TEXT = "نوع مقصد را بنویسید: کانال یا گروه"
ASK_DESTINATION_ACCESS_TEXT = "سطح دسترسی را بنویسید: عمومی یا خصوصی"
ASK_PRIORITY = "صرافی‌ها را به ترتیب انتخاب کنید."
EDIT_PRIORITY = "اولویت صرافی‌ها را ویرایش کنید."
PRIORITY_CURRENT = "اولویت فعلی: {value}"
EMPTY_VALUE = "خالی"
NOT_RECORDED = "ثبت نشده"
TYPE_CHANNEL = "کانال"
TYPE_GROUP = "گروه"
ACCESS_PUBLIC = "عمومی"
ACCESS_PRIVATE = "خصوصی"
SHOW_ELIGIBLE = "نمایش واجدان شرایط"
SHOW_ASSIGNED = "نمایش انتساب‌های فعلی"
CONFIRM_UNASSIGN = "آیا از حذف این انتساب مطمئن هستید؟"
CONFIRM_METADATA = "تغییر مشخصات تأیید شود؟"
CONFIRM_ARCHIVE = "آیا از بایگانی این سیگنالیست مطمئن هستید؟"
CONFIRM_ARCHIVE_BUTTON = "تأیید بایگانی"
ASSIGNED_DESTINATIONS_TITLE = "<b>مقصدهای اختصاص‌یافته</b>\nصفحه {page} از {pages}"
ADD_EXCHANGE = "افزودن {exchange}"


def safe(value: object) -> str:
    return escape(str(value), quote=True)


def welcome_signalist(display_name: str) -> str:
    return WELCOME_SIGNALIST.format(name=safe(display_name))


def status_signalist(display_name: str) -> str:
    return STATUS_SIGNALIST.format(name=safe(display_name))

def page_title(template: str, page: int, pages: int) -> str:
    return template.format(page=safe(page), pages=safe(pages))

def signalist_line(item) -> str:
    labels={"active":"فعال","inactive":"غیرفعال","archived":"بایگانی‌شده"}
    return f"{safe(item.display_name)} — {safe(labels[item.status.value])} — #{safe(item.id)}"

def destination_line(item) -> str:
    labels={"active":"فعال","inactive":"غیرفعال"}
    return f"{safe(item.internal_name)} — {safe(labels[item.status.value])} — #{safe(item.id)}"

STATUS_LABELS = {"active":"فعال", "inactive":"غیرفعال", "archived":"بایگانی‌شده"}
TYPE_LABELS = {"channel":"کانال", "group":"گروه"}
ACCESS_LABELS = {"public":"عمومی", "private":"خصوصی"}

def priority_current(values) -> str:
    return PRIORITY_CURRENT.format(value=safe("، ".join(values) or EMPTY_VALUE))

def signalist_review(data, username) -> str:
    return f"نام: {safe(data['display_name'])}\nشناسه: {safe(data['telegram_user_id'])}\nنام کاربری: {safe(username or NOT_RECORDED)}"

def signalist_detail(item, count) -> str:
    return (f"<b>{safe(item.display_name)}</b>\nشناسه داخلی: {safe(item.id)}\nشناسه تلگرام: {safe(item.telegram_user_id)}\n"
            f"نام کاربری: {safe(item.telegram_username or NOT_RECORDED)}\nوضعیت: {safe(STATUS_LABELS[item.status.value])}\nتعداد مقصدها: {safe(count)}\n"
            f"ایجاد: {safe(item.created_at)}\nبه‌روزرسانی: {safe(item.updated_at)}" +
            (f"\nبایگانی: {safe(item.archived_at)}" if item.archived_at else ""))

def destination_detail(detail) -> str:
    d=detail.destination
    return (f"<b>{safe(d.internal_name)}</b>\nشناسه داخلی: {safe(d.id)}\nشناسه چت: {safe(d.telegram_chat_id)}\nنوع: {safe(TYPE_LABELS[d.destination_type.value])}\n"
            f"دسترسی: {safe(ACCESS_LABELS[d.access_level.value])}\nوضعیت: {safe(STATUS_LABELS[d.status.value])}\nسقف روزانه: {safe(d.daily_limit)}\n"
            f"اولویت: {safe('، '.join(x.value for x in detail.priority.exchanges))}\nسیگنالیست‌ها: {safe(detail.assigned_count)}\n"
            f"ایجاد: {safe(d.created_at)}\nبه‌روزرسانی: {safe(d.updated_at)}")

def destination_review(data) -> str:
    return (f"<b>بازبینی مقصد</b>\nنام داخلی: {safe(data['internal_name'])}\nشناسه چت: {safe(data['telegram_chat_id'])}\n"
            f"نوع: {safe(TYPE_LABELS[data['destination_type']])}\nدسترسی: {safe(ACCESS_LABELS[data['access_level']])}\n"
            f"سقف روزانه: {safe(data['daily_limit'])}\nاولویت: {safe('، '.join(data['exchanges']))}\nوضعیت اولیه: غیرفعال")

def metadata_review(data) -> str:
    return (f"<b>بازبینی تغییر مشخصات</b>\nنام داخلی: {safe(data['internal_name'])}\nنوع: {safe(TYPE_LABELS[data['destination_type']])}\n"
            f"دسترسی: {safe(ACCESS_LABELS[data['access_level']])}\nسقف روزانه: {safe(data['daily_limit'])}")
