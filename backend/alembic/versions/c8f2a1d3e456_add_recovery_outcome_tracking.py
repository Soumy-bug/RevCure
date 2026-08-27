"""add_recovery_outcome_tracking

Revision ID: c8f2a1d3e456
Revises: b475926cf903
Create Date: 2026-08-27 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8f2a1d3e456'
down_revision: Union[str, Sequence[str], None] = 'b475926cf903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add outcome tracking columns to recovery_attempts."""
    op.add_column('recovery_attempts', sa.Column('amount', sa.Integer(), nullable=True))
    op.add_column('recovery_attempts', sa.Column('razorpay_order_id', sa.String(length=255), nullable=True))
    op.add_column('recovery_attempts', sa.Column('outcome', sa.String(length=20), nullable=False, server_default='executed'))
    op.add_column('recovery_attempts', sa.Column('recovered_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove outcome tracking columns from recovery_attempts."""
    op.drop_column('recovery_attempts', 'recovered_at')
    op.drop_column('recovery_attempts', 'outcome')
    op.drop_column('recovery_attempts', 'razorpay_order_id')
    op.drop_column('recovery_attempts', 'amount')
