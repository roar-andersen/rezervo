from datetime import datetime, timezone

import pytz

from rezervo.consts import WEEKDAYS
from rezervo.schemas.config.user import ChainConfig, Class
from rezervo.schemas.schedule import RezervoClass, RezervoDay, RezervoSchedule


def _matches_class_config(d: RezervoDay, c: RezervoClass, cc: Class) -> bool:
    if c.location.id != cc.location_id:
        return False
    if c.activity.id != str(cc.activity_id):
        return False
    localized_start_time = c.start_time.astimezone(
        pytz.timezone("Europe/Oslo")
    )  # TODO: clean this
    if not (
        localized_start_time.hour == cc.start_time.hour
        and localized_start_time.minute == cc.start_time.minute
    ):
        return False
    if cc.specific_date is not None:
        return d.date == cc.specific_date.isoformat()
    return d.day_name == WEEKDAYS[cc.weekday]


def get_user_planned_sessions_from_schedule(
    chain_config: ChainConfig, schedule: RezervoSchedule
) -> list[RezervoClass]:
    if not chain_config.active:
        return []
    all_bookings = list(chain_config.recurring_bookings) + list(
        chain_config.one_time_bookings
    )
    classes: list[RezervoClass] = []
    for d in schedule.days:
        for c in d.classes:
            for cc in all_bookings:
                if not _matches_class_config(d, c, cc):
                    continue
                # check if booking_opens_at is in the past (if so, it is either already booked or will not be booked)
                if c.booking_opens_at < datetime.now(timezone.utc):
                    continue
                classes.append(c)
    return classes
