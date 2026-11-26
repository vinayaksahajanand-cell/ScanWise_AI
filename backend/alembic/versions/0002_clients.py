"""clients table (New Client form, business-card scanner)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 16:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('clients',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('company', sa.String(length=255), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('phone', sa.String(length=64), nullable=True),
    sa.Column('job_title', sa.String(length=255), nullable=True),
    sa.Column('website', sa.String(length=255), nullable=True),
    sa.Column('street', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('state', sa.String(length=120), nullable=True),
    sa.Column('zip_code', sa.String(length=32), nullable=True),
    sa.Column('country', sa.String(length=120), nullable=True),
    sa.Column('tax_id', sa.String(length=64), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_clients_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_clients_name'), ['name'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('clients', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clients_name'))
        batch_op.drop_index(batch_op.f('ix_clients_created_at'))
    op.drop_table('clients')
