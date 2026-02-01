import datetime

import pytz

from rezervo.schemas.config.user import Class
from rezervo.schemas.schedule import BaseRezervoClass


def class_config_recurrent_id(class_config: Class) -> str:
    if class_config.specific_date is not None:
        return one_time_class_id(
            class_config.activity_id,
            class_config.specific_date,
            class_config.start_time.hour,
            class_config.start_time.minute,
        )
    return recurrent_class_id(
        class_config.activity_id,
        class_config.weekday,
        class_config.start_time.hour,
        class_config.start_time.minute,
    )


def rezervo_class_recurrent_id(_class: BaseRezervoClass) -> str:
    localized_start_time = _class.start_time.astimezone(
        pytz.timezone("Europe/Oslo")
    )  # TODO: clean this
    return recurrent_class_id(
        _class.activity.id,
        localized_start_time.weekday(),
        localized_start_time.hour,
        localized_start_time.minute,
    )


def rezervo_class_one_time_id(
    _class: BaseRezervoClass, specific_date: datetime.date
) -> str:
    localized_start_time = _class.start_time.astimezone(
        pytz.timezone("Europe/Oslo")
    )  # TODO: clean this
    return one_time_class_id(
        _class.activity.id,
        specific_date,
        localized_start_time.hour,
        localized_start_time.minute,
    )


def recurrent_class_id(activity_id: str, weekday: int, hour: int, minute: int) -> str:
    return f"{activity_id}_{weekday}_{hour}_{minute}"


def one_time_class_id(
    activity_id: str, specific_date: datetime.date, hour: int, minute: int
) -> str:
    return f"{activity_id}_d{specific_date.isoformat()}_{hour}_{minute}"
