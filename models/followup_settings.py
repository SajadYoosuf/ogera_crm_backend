"""Per-tenant follow-up timings, reminder rules and staff permissions (one row per tenant schema).

The table is created by scripts/migrate_followup_settings.sql. Code never assumes it exists:
services/followup_settings.get_settings() falls back to defaults when the table or its row is missing.
"""
import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class FollowupSettings(Base):
    __tablename__ = "followup_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    default_due_time: Mapped[datetime.time] = mapped_column(Time, default=datetime.time(9, 0))
    morning_time: Mapped[datetime.time] = mapped_column(Time, default=datetime.time(10, 0))
    afternoon_time: Mapped[datetime.time] = mapped_column(Time, default=datetime.time(14, 0))
    evening_time: Mapped[datetime.time] = mapped_column(Time, default=datetime.time(17, 0))

    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_mode: Mapped[str] = mapped_column(String(20), default="due_day_only")  # due_day_only | daily_until_done
    reminder_max_days: Mapped[int] = mapped_column(Integer, default=7)  # 0 = no limit
    due_today_reminder_hour: Mapped[int] = mapped_column(SmallInteger, default=9)  # IST hour
    due_now_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    due_now_window_minutes: Mapped[int] = mapped_column(SmallInteger, default=10)
    overdue_reminder_after_hours: Mapped[int] = mapped_column(SmallInteger, default=24)

    retry_followup_days: Mapped[int] = mapped_column(SmallInteger, default=3)
    auto_followup_hours: Mapped[int] = mapped_column(SmallInteger, default=48)

    allow_staff_reschedule: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_staff_create_followup: Mapped[bool] = mapped_column(Boolean, default=True)

    # Badge colour per task status value, e.g. {"todo": "gray", "inprogress": "blue"}. Labels live in enum_values.
    status_colors: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
