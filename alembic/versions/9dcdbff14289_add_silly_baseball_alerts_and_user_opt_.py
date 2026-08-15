"""add silly baseball alerts and user opt-in

Revision ID: 9dcdbff14289
Revises: cbc1308ba394
Create Date: 2026-08-14 19:36:51.973468

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9dcdbff14289'
down_revision: str | Sequence[str] | None = 'cbc1308ba394'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    if 'silly_baseball_alerts' not in tables:
        op.create_table(
            'silly_baseball_alerts',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('game_pk', sa.Integer(), nullable=False),
            sa.Column('alert_type', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('game_pk', 'alert_type'),
        )
        op.create_index(op.f('ix_silly_baseball_alerts_alert_type'), 'silly_baseball_alerts', ['alert_type'], unique=False)
        op.create_index(op.f('ix_silly_baseball_alerts_game_pk'), 'silly_baseball_alerts', ['game_pk'], unique=False)

    cols = {c['name'] for c in sa.inspect(conn).get_columns('users')}
    if 'silly_baseball_alerts' not in cols:
        op.add_column('users', sa.Column('silly_baseball_alerts', sa.Boolean(), nullable=True))
    indexes = {i['name'] for i in sa.inspect(conn).get_indexes('users')}
    if 'ix_users_silly_baseball_alerts' not in indexes:
        op.create_index(op.f('ix_users_silly_baseball_alerts'), 'users', ['silly_baseball_alerts'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_users_silly_baseball_alerts'), table_name='users')
    op.drop_column('users', 'silly_baseball_alerts')
    op.drop_index(op.f('ix_silly_baseball_alerts_game_pk'), table_name='silly_baseball_alerts')
    op.drop_index(op.f('ix_silly_baseball_alerts_alert_type'), table_name='silly_baseball_alerts')
    op.drop_table('silly_baseball_alerts')
