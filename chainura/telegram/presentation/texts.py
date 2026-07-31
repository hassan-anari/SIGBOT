from __future__ import annotations

from html import escape

MENU_MAIN = "منوی اصلی"
MENU_ADMIN_STATUS = "وضعیت دسترسی"
MENU_SIGNALIST_STATUS = "وضعیت حساب"
MENU_HELP = "راهنما"
BUTTON_BACK = "بازگشت به منوی اصلی"
BUTTON_REFRESH = "تازه‌سازی وضعیت"

WELCOME_ADMIN = "<b>خوش آمدید.</b>\nشما مدیر اصلی سامانه هستید."
WELCOME_SIGNALIST = "<b>خوش آمدید، {name}.</b>\nحساب سیگنالیست شما فعال است."
DASHBOARD_ADMIN = "<b>منوی اصلی مدیر</b>\nیکی از گزینه‌های موجود را انتخاب کنید."
DASHBOARD_SIGNALIST = "<b>منوی اصلی سیگنالیست</b>\nیکی از گزینه‌های موجود را انتخاب کنید."
HELP_ADMIN = "<b>راهنمای مدیر</b>\nدر این مرحله فقط منوی اصلی و وضعیت دسترسی در دسترس است."
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


def safe(value: object) -> str:
    return escape(str(value), quote=True)


def welcome_signalist(display_name: str) -> str:
    return WELCOME_SIGNALIST.format(name=safe(display_name))


def status_signalist(display_name: str) -> str:
    return STATUS_SIGNALIST.format(name=safe(display_name))
