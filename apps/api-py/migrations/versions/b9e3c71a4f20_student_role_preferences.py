"""A student's role preferences, in priority order.

Revision ID: b9e3c71a4f20
Revises: daaeb23b750e
Create Date: 2026-10-22

`students.role_preferences` is a JSONB list of role names, first choice first,
loaded by the Main Admin from the college's role-preference form. NO BACKFILL:
NULL on every existing row is the truth until the office loads the sheet.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b9e3c71a4f20"
down_revision: Union[str, None] = "daaeb23b750e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "students",
        sa.Column("role_preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("students", "role_preferences")
