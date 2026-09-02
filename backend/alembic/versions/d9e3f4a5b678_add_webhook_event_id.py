from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e3f4a5b678'
down_revision: Union[str, Sequence[str], None] = 'c8f2a1d3e456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add webhook_event_id column for Razorpay webhook deduplication."""
    op.add_column('events', sa.Column('webhook_event_id', sa.String(length=255), nullable=True))
    op.create_index('ix_events_webhook_event_id', 'events', ['webhook_event_id'], unique=True)


def downgrade() -> None:
    """Remove webhook_event_id column."""
    op.drop_index('ix_events_webhook_event_id', table_name='events')
    op.drop_column('events', 'webhook_event_id')
