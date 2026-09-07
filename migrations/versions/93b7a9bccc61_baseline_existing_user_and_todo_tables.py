"""baseline existing user and todo tables

Revision ID: 93b7a9bccc61
Revises: 
Create Date: 2026-09-07 21:47:00.111255

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '93b7a9bccc61'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create the pre-migration schema.

    On the existing database these tables are already present, so this
    revision is stamped rather than run. It exists so a fresh database can
    be built from an empty state.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if 'user' not in existing:
        op.create_table(
            'user',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('username', sa.String(length=50), nullable=False),
            sa.Column('password_hash', sa.String(length=255), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('username'),
        )

    if 'todo' not in existing:
        op.create_table(
            'todo',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('content', sa.String(length=200), nullable=False),
            sa.Column('date_created', sa.DateTime(), nullable=True),
            sa.Column('completed', sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    op.drop_table('todo')
    op.drop_table('user')
