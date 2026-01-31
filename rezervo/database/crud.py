import datetime as dt
import uuid
from collections import defaultdict
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session
from starlette import status

from rezervo import models
from rezervo.auth.jwt import decode_jwt_sub
from rezervo.models import SessionState, UserRelation
from rezervo.schemas.community import (
    Community,
    CommunityUser,
    UserRelationship,
    UserRelationshipAction,
)
from rezervo.schemas.config import admin
from rezervo.schemas.config.admin import AdminConfig
from rezervo.schemas.config.app import AppConfig
from rezervo.schemas.config.config import (
    Config,
    PushNotificationSubscription,
    PushNotificationSubscriptionKeys,
    config_from_stored,
)
from rezervo.schemas.config.user import (
    ChainConfig,
    ChainIdentifier,
    ChainUser,
    ChainUserCredentials,
    ChainUserProfile,
    Class,
    ClassTime,
    UserPreferences,
    config_from_chain_user,
)
from rezervo.schemas.schedule import UserSession, session_model_from_user_session
from rezervo.utils.ical_utils import generate_calendar_token


def user_from_token(db: Session, app_config: AppConfig, token) -> Optional[models.User]:
    fusionauth_config = app_config.fusionauth
    jwt_sub = decode_jwt_sub(
        token.credentials,
        fusionauth_config.jwt_algorithms,
        str(fusionauth_config.application_id),
        fusionauth_config.issuer,
    )
    if jwt_sub is None:
        return None
    return db.query(models.User).filter_by(jwt_sub=jwt_sub).one_or_none()


def create_user(db: Session, name: str, jwt_sub: str, slack_id: Optional[str] = None):
    db_user = models.User(
        name=name,
        jwt_sub=jwt_sub,
        cal_token=generate_calendar_token(),
        admin_config=admin.AdminConfig(
            notifications=(
                admin.Notifications(slack=admin.Slack(user_id=slack_id))
                if slack_id is not None
                else None
            ),
        ).dict(),
        preferences=UserPreferences().dict(),
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def get_chain_user_creds(
    db: Session, user_id: UUID, chain_identifier: ChainIdentifier
) -> Optional[ChainUserCredentials]:
    db_chain_user = get_db_chain_user(db, chain_identifier, user_id)
    if db_chain_user is None:
        return None
    return ChainUserCredentials(
        username=db_chain_user.username, password=db_chain_user.password
    )


def upsert_chain_user_creds(
    db: Session,
    user_id: UUID,
    chain_identifier: ChainIdentifier,
    creds: ChainUserCredentials,
    mark_as_verified: bool = True,
):
    db_chain_user = get_db_chain_user(db, chain_identifier, user_id)
    if db_chain_user is None:
        db_chain_user = models.ChainUser(
            user_id=user_id,
            chain=chain_identifier,
            username=creds.username,
            password=creds.password,
        )
        if mark_as_verified:
            db_chain_user.auth_verified_at = dt.datetime.now()
        db.add(db_chain_user)
    else:
        if mark_as_verified:
            db_chain_user.auth_verified_at = dt.datetime.now()
        if (
            db_chain_user.username == creds.username
            and db_chain_user.password == creds.password
        ):
            return db_chain_user
        if mark_as_verified:
            db_chain_user.auth_data = None
        db_chain_user.username = creds.username
        db_chain_user.password = creds.password
    db.commit()
    db.refresh(db_chain_user)
    return db_chain_user


def upsert_chain_user_auth_data(
    db: Session,
    chain_identifier: ChainIdentifier,
    user_id: UUID,
    auth_data: Optional[str],
):
    db.query(models.ChainUser).filter_by(
        user_id=user_id, chain=chain_identifier
    ).update({models.ChainUser.auth_data: auth_data})
    db.commit()


def get_db_chain_user(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
) -> Optional[models.ChainUser]:
    return (
        db.query(models.ChainUser)
        .filter_by(user_id=user_id, chain=chain_identifier)
        .one_or_none()
    )


def get_chain_user_totp(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
) -> Optional[str]:
    return (
        db.query(models.ChainUser.totp)
        .filter_by(user_id=user_id, chain=chain_identifier)
        .scalar()
    )


def get_chain_user_auth_verified_at(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
) -> Optional[dt.datetime]:
    return (
        db.query(models.ChainUser.auth_verified_at)
        .filter_by(user_id=user_id, chain=chain_identifier)
        .scalar()
    )


def update_chain_user_auth_verified_at(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
):
    db.query(models.ChainUser).filter_by(
        user_id=user_id, chain=chain_identifier
    ).update({models.ChainUser.auth_verified_at: dt.datetime.now()})
    db.commit()


def delete_chain_user_totp(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
):
    db.query(models.ChainUser).filter_by(
        user_id=user_id, chain=chain_identifier
    ).update({models.ChainUser.totp: None})
    db.commit()


def get_chain_user(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
) -> Optional[ChainUser]:
    db_chain_user = get_db_chain_user(db, chain_identifier, user_id)
    if db_chain_user is None:
        return None
    return _get_chain_user_from_db_model(db, db_chain_user)


def _class_from_db_booking(db_booking: models.RecurringBooking) -> Class:
    return Class(
        **db_booking.__dict__,
        start_time=ClassTime(
            hour=db_booking.start_time_hour,
            minute=db_booking.start_time_minute,
        ),
    )


def _get_chain_user_from_db_model(
    db: Session, db_chain_user: models.ChainUser
) -> ChainUser:
    all_bookings = list(
        db.query(models.RecurringBooking).filter_by(
            user_id=db_chain_user.user_id,
            chain_id=db_chain_user.chain,
        )
    )
    return ChainUser(
        **db_chain_user.__dict__,
        recurring_bookings=[
            _class_from_db_booking(b) for b in all_bookings if b.specific_date is None
        ],
        one_time_bookings=[
            _class_from_db_booking(b)
            for b in all_bookings
            if b.specific_date is not None
        ],
    )


def get_chain_users(
    db: Session, chain_identifier: ChainIdentifier, active_only: bool = False
) -> list[ChainUser]:
    return [
        _get_chain_user_from_db_model(db, db_chain_user)
        for db_chain_user in (
            (db.query(models.ChainUser).filter_by(chain=chain_identifier, active=True))
            if active_only
            else db.query(models.ChainUser).filter_by(chain=chain_identifier)
        )
    ]


def get_chain_config(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
) -> Optional[ChainConfig]:
    user = get_chain_user(db, chain_identifier, user_id)
    if user is None:
        return None
    return config_from_chain_user(user)


def get_chain_user_profile(
    db: Session, chain_identifier: ChainIdentifier, user_id: UUID
) -> Optional[ChainUserProfile]:
    user = get_chain_user(db, chain_identifier, user_id)
    if user is None:
        return None
    return ChainUserProfile(
        username=user.username, is_auth_verified=user.auth_verified_at is not None
    )


def _upsert_bookings(
    db: Session,
    user_id: UUID,
    chain_id: str,
    bookings: list[Class],
    is_one_time: bool,
) -> list[uuid.UUID]:
    """Upsert bookings and return IDs of kept (existing) bookings."""
    kept_ids: list[uuid.UUID] = []
    for c in bookings:
        filter_kwargs = {
            "user_id": user_id,
            "chain_id": chain_id,
            "activity_id": c.activity_id,
            "weekday": c.weekday,
            "location_id": c.location_id,
            "start_time_hour": c.start_time.hour,
            "start_time_minute": c.start_time.minute,
            "specific_date": c.specific_date if is_one_time else None,
        }
        db_booking = (
            db.query(models.RecurringBooking).filter_by(**filter_kwargs).one_or_none()
        )
        if db_booking is None:
            db.add(
                models.RecurringBooking(
                    **filter_kwargs,
                    display_name=c.display_name,
                )
            )
        else:
            db_booking.display_name = c.display_name
            kept_ids.append(db_booking.id)
    return kept_ids


def update_chain_config(
    db: Session, user_id: UUID, config: ChainConfig
) -> Optional[ChainConfig]:
    db_chain_user: Optional[models.ChainUser] = (
        db.query(models.ChainUser)
        .filter_by(user_id=user_id, chain=config.chain)
        .one_or_none()
    )
    if db_chain_user is None:
        return None
    db_chain_user.active = config.active
    kept_recurring = _upsert_bookings(
        db, user_id, config.chain, config.recurring_bookings, is_one_time=False
    )
    # Preserve existing one-time bookings - they are managed via dedicated endpoints
    existing_one_time_ids = [
        b.id
        for b in db.query(models.RecurringBooking).filter_by(
            user_id=user_id,
            chain_id=config.chain,
        )
        if b.specific_date is not None
    ]
    kept_ids = kept_recurring + existing_one_time_ids
    # Remove recurring bookings not part of existing bookings for this user
    # (one-time bookings are preserved)
    db.query(models.RecurringBooking).filter_by(
        user_id=user_id,
        chain_id=config.chain,
    ).filter(
        models.RecurringBooking.specific_date.is_(None),
        ~models.RecurringBooking.id.in_(kept_ids),
    ).delete()
    db.commit()
    db.refresh(db_chain_user)
    return config_from_chain_user(_get_chain_user_from_db_model(db, db_chain_user))


def add_one_time_booking(
    db: Session,
    user_id: UUID,
    chain_identifier: ChainIdentifier,
    class_config: Class,
) -> Optional[ChainConfig]:
    db_chain_user = get_db_chain_user(db, chain_identifier, user_id)
    if db_chain_user is None:
        return None
    if class_config.specific_date is None:
        return None
    existing = (
        db.query(models.RecurringBooking)
        .filter_by(
            user_id=user_id,
            chain_id=chain_identifier,
            activity_id=class_config.activity_id,
            weekday=class_config.weekday,
            location_id=class_config.location_id,
            start_time_hour=class_config.start_time.hour,
            start_time_minute=class_config.start_time.minute,
            specific_date=class_config.specific_date,
        )
        .one_or_none()
    )
    if existing is not None:
        return config_from_chain_user(_get_chain_user_from_db_model(db, db_chain_user))
    db.add(
        models.RecurringBooking(
            user_id=user_id,
            chain_id=chain_identifier,
            activity_id=class_config.activity_id,
            weekday=class_config.weekday,
            location_id=class_config.location_id,
            start_time_hour=class_config.start_time.hour,
            start_time_minute=class_config.start_time.minute,
            display_name=class_config.display_name,
            specific_date=class_config.specific_date,
        )
    )
    db.commit()
    db.refresh(db_chain_user)
    return config_from_chain_user(_get_chain_user_from_db_model(db, db_chain_user))


def remove_one_time_booking(
    db: Session,
    user_id: UUID,
    chain_identifier: ChainIdentifier,
    class_config: Class,
) -> Optional[ChainConfig]:
    db_chain_user = get_db_chain_user(db, chain_identifier, user_id)
    if db_chain_user is None:
        return None
    if class_config.specific_date is None:
        return None
    db.query(models.RecurringBooking).filter_by(
        user_id=user_id,
        chain_id=chain_identifier,
        activity_id=class_config.activity_id,
        weekday=class_config.weekday,
        location_id=class_config.location_id,
        start_time_hour=class_config.start_time.hour,
        start_time_minute=class_config.start_time.minute,
        specific_date=class_config.specific_date,
    ).delete()
    db.commit()
    db.refresh(db_chain_user)
    return config_from_chain_user(_get_chain_user_from_db_model(db, db_chain_user))


def purge_expired_one_time_bookings(db: Session) -> int:
    # Use Oslo timezone for Norwegian gym chains
    oslo_tz = ZoneInfo("Europe/Oslo")
    today = dt.datetime.now(oslo_tz).date()
    row_count = (
        db.query(models.RecurringBooking)
        .filter(
            models.RecurringBooking.specific_date.isnot(None),
            models.RecurringBooking.specific_date < today,
        )
        .delete()
    )
    db.commit()
    return row_count


def delete_one_time_booking_by_id(
    db: Session,
    user_id: UUID,
    chain_identifier: ChainIdentifier,
    booking_id: uuid.UUID,
) -> bool:
    row_count = (
        db.query(models.RecurringBooking)
        .filter_by(
            id=booking_id,
            user_id=user_id,
            chain_id=chain_identifier,
        )
        .filter(models.RecurringBooking.specific_date.isnot(None))
        .delete()
    )
    db.commit()
    return row_count > 0


def delete_user(db: Session, user_id: UUID):
    db_user = db.get(models.User, user_id)
    db.delete(db_user)
    db.commit()


def upsert_user_chain_sessions(
    db: Session,
    user_id: UUID,
    chain_identifier: ChainIdentifier,
    user_sessions: list[UserSession],
):
    # delete unconfirmed sessions
    db.execute(
        delete(models.Session).where(
            models.Session.user_id == user_id,
            models.Session.chain == chain_identifier,
            models.Session.status != SessionState.CONFIRMED,
            models.Session.status != SessionState.NOSHOW,
        )
    )
    for s in user_sessions:
        db.merge(session_model_from_user_session(s))
    db.commit()


def get_user(db, user_id) -> Optional[models.User]:
    return db.query(models.User).filter_by(id=user_id).one_or_none()


def get_user_config_by_id(db, user_id) -> Optional[Config]:
    db_user = get_user(db, user_id)
    if db_user is None:
        return None
    return get_user_config(db, db_user)


def get_user_push_notification_subscriptions(
    db, user_id: UUID
) -> list[PushNotificationSubscription]:
    return [
        PushNotificationSubscription(
            endpoint=db_subscription.endpoint,
            keys=PushNotificationSubscriptionKeys(**db_subscription.keys),
        )
        for db_subscription in db.query(models.PushNotificationSubscription).filter_by(
            user_id=user_id
        )
    ]


def update_last_used_push_notification_subscription(
    db, subscription: PushNotificationSubscription
):
    db_subscription: models.PushNotificationSubscription = (
        db.query(models.PushNotificationSubscription)
        .filter_by(endpoint=subscription.endpoint)
        .one_or_none()
    )
    if db_subscription is None:
        return
    db_subscription.last_used = dt.datetime.now()
    db.commit()


def get_user_config(db, user: models.User) -> Config:
    return config_from_stored(
        user.id,
        UserPreferences(**user.preferences),
        get_user_push_notification_subscriptions(db, user.id),
        AdminConfig(**user.admin_config),
    )


def get_user_config_by_slack_id(db, slack_id) -> Optional[Config]:
    if slack_id is None:
        return None
    for u in db.query(models.User).all():
        user_config = get_user_config(db, u)
        config = user_config.config
        if config.notifications is None:
            continue
        if config.notifications.slack is None:
            continue
        if config.notifications.slack.user_id == slack_id:
            return user_config
    return None


def upsert_push_notification_subscription(
    db, user_id, subscription: PushNotificationSubscription
):
    db_subscription = (
        db.query(models.PushNotificationSubscription)
        .filter_by(user_id=user_id, endpoint=subscription.endpoint)
        .one_or_none()
    )
    if db_subscription is None:
        db_subscription = models.PushNotificationSubscription(
            user_id=user_id,
            endpoint=subscription.endpoint,
            keys=subscription.keys.dict(),
        )
        db.add(db_subscription)
    else:
        db_subscription.keys = subscription.keys
    db.commit()
    db.refresh(db_subscription)
    return db_subscription


def delete_push_notification_subscription(
    db, subscription: PushNotificationSubscription, user_id: Optional[UUID] = None
) -> bool:
    db_subscription_query = db.query(models.PushNotificationSubscription).filter_by(
        endpoint=subscription.endpoint,
        keys=subscription.keys.dict(),
    )
    if user_id is not None:
        db_subscription_query = db_subscription_query.filter_by(user_id=user_id)
    db_subscription = db_subscription_query.one_or_none()
    if db_subscription is None:
        return False
    db.delete(db_subscription)
    db.commit()
    return True


def verify_push_notification_subscription(
    db, user_id: UUID, subscription: PushNotificationSubscription
) -> bool:
    return (
        db.query(models.PushNotificationSubscription)
        .filter_by(
            user_id=user_id,
            endpoint=subscription.endpoint,
            keys=subscription.keys.dict(),
        )
        .one_or_none()
    ) is not None


def purge_slack_receipts(db) -> int:
    row_count = (
        db.query(models.SlackClassNotificationReceipt)
        .filter(models.SlackClassNotificationReceipt.expires_at < dt.datetime.now())
        .delete()
    )
    db.commit()
    return row_count


def get_user_relationship_index(db: Session, user_id: UUID):
    relationships = (
        db.query(UserRelation)
        .filter((UserRelation.user_one == user_id) | (UserRelation.user_two == user_id))
        .all()
    )

    user_relationship_index = {}
    for relationship in relationships:
        other_user_id = (
            relationship.user_two
            if relationship.user_one == user_id
            else relationship.user_one
        )
        # Make sure the perspective of the friend request is correct
        relationship_status = (
            UserRelationship.REQUEST_RECEIVED
            if relationship.user_two == user_id
            and relationship.relationship == UserRelationship.REQUEST_SENT
            else relationship.relationship
        )
        user_relationship_index[other_user_id] = relationship_status
    return user_relationship_index


def get_friend_ids_in_class(db: Session, user_id: UUID, class_id: str) -> list[UUID]:
    return list(
        db.scalars(
            select(models.Session.user_id)
            .join(
                UserRelation,
                or_(
                    and_(
                        UserRelation.user_one == models.Session.user_id,
                        UserRelation.user_two == user_id,
                    ),
                    and_(
                        UserRelation.user_two == models.Session.user_id,
                        UserRelation.user_one == user_id,
                    ),
                ),
            )
            .filter(
                UserRelation.relationship == UserRelationship.FRIEND,
                models.Session.class_id == class_id,
            )
        ).all()
    )


def get_community(db: Session, user_id: UUID) -> Community:
    users = (
        db.query(models.User)
        .filter(models.User.id != user_id)
        .order_by(models.User.name)
        .all()
    )
    chain_users = (
        db.query(models.ChainUser).filter(models.ChainUser.user_id != user_id).all()
    )

    user_to_chain_map: defaultdict[UUID, list[str]] = defaultdict(list)
    for chain_user in chain_users:
        user_to_chain_map[chain_user.user_id].append(chain_user.chain)

    user_relationship_index = get_user_relationship_index(db, user_id)

    return Community(
        users=[
            CommunityUser(
                user_id=user.id,
                name=user.name,
                chains=user_to_chain_map.get(user.id, []),
                relationship=user_relationship_index.get(
                    user.id, UserRelationship.UNKNOWN
                ),
            )
            for user in users
        ]
    )


def modify_user_relationship(
    db: Session, user_id: UUID, other_user_id: UUID, action: UserRelationshipAction
):
    if not db.query(models.User).filter(models.User.id == other_user_id).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Other user not found"
        )

    existing_relation = (
        db.query(UserRelation)
        .filter(
            (
                (UserRelation.user_one == user_id)
                & (UserRelation.user_two == other_user_id)
            )
            | (
                (UserRelation.user_one == other_user_id)
                & (UserRelation.user_two == user_id)
            )
        )
        .first()
    )

    if action == UserRelationshipAction.ADD_FRIEND:
        if existing_relation:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Relationship already exists",
            )
        new_relation = UserRelation(
            user_one=user_id,
            user_two=other_user_id,
            relationship=UserRelationship.REQUEST_SENT,
        )
        db.add(new_relation)
        db.commit()

        return UserRelationship.REQUEST_SENT

    if action in [
        UserRelationshipAction.REMOVE_FRIEND,
        UserRelationshipAction.DENY_FRIEND,
    ]:
        if existing_relation:
            db.delete(existing_relation)
            db.commit()
        return UserRelationship.UNKNOWN

    if action == UserRelationshipAction.ACCEPT_FRIEND:
        if not existing_relation or existing_relation.user_two != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        existing_relation.relationship = UserRelationship.FRIEND
        db.commit()
        return UserRelationship.FRIEND

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
