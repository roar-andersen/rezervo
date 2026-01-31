"""add_specific_date_to_recurring_bookings

Revision ID: a1b2c3d4e5f6
Revises: 27d034ace1bf
Create Date: 2026-01-31 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "27d034ace1bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recurring_bookings",
        sa.Column("specific_date", sa.Date(), nullable=True),
    )
    op.drop_constraint("unique_recurring_booking", "recurring_bookings", type_="unique")
    # Partial unique index for recurring bookings (specific_date IS NULL)
    # This prevents duplicate recurring bookings since NULL != NULL in SQL
    op.create_index(
        "unique_recurring_booking_idx",
        "recurring_bookings",
        [
            "user_id",
            "chain_id",
            "location_id",
            "activity_id",
            "weekday",
            "start_time_hour",
            "start_time_minute",
        ],
        unique=True,
        postgresql_where=sa.text("specific_date IS NULL"),
    )
    # Unique constraint for one-time bookings (specific_date IS NOT NULL)
    op.create_unique_constraint(
        "unique_one_time_booking",
        "recurring_bookings",
        [
            "user_id",
            "chain_id",
            "location_id",
            "activity_id",
            "weekday",
            "start_time_hour",
            "start_time_minute",
            "specific_date",
        ],
    )


def downgrade() -> None:
    op.drop_constraint("unique_one_time_booking", "recurring_bookings", type_="unique")
    op.drop_index("unique_recurring_booking_idx", "recurring_bookings")
    # Delete one-time bookings before restoring the old constraint
    op.execute("DELETE FROM recurring_bookings WHERE specific_date IS NOT NULL")
    op.create_unique_constraint(
        "unique_recurring_booking",
        "recurring_bookings",
        [
            "user_id",
            "chain_id",
            "location_id",
            "activity_id",
            "weekday",
            "start_time_hour",
            "start_time_minute",
        ],
    )
    op.drop_column("recurring_bookings", "specific_date")
