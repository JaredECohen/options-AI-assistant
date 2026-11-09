"""initial schema: users, conversations/messages, positions, saved screens, LLM usage ledger, jobs, market data (P3)

Revision ID: 0001
Revises: 
Create Date: 2026-11-08 02:36:33.489066
"""
import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reviewed: initial OptionsCoach schema (user state, usage ledger, jobs, market data).
    op.create_table('ingest_batches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rows_written', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('option_contracts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('occ_symbol', sa.String(length=32), nullable=False),
    sa.Column('underlying', sa.String(length=16), nullable=False),
    sa.Column('expiration', sa.Date(), nullable=False),
    sa.Column('strike', sa.Float(), nullable=False),
    sa.Column('option_type', sa.String(length=4), nullable=False),
    sa.Column('multiplier', sa.Integer(), nullable=False),
    sa.Column('adjusted', sa.Boolean(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('occ_symbol')
    )
    with op.batch_alter_table('option_contracts', schema=None) as batch_op:
        batch_op.create_index('ix_option_contracts_underlying_exp', ['underlying', 'expiration'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('auth_provider', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_id')
    )
    op.create_table('conversations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.create_index('ix_conversations_user_updated', ['user_id', 'updated_at'], unique=False)

    op.create_table('iv_daily',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('iv', sa.Float(), nullable=False),
    sa.Column('method', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('batch_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['ingest_batches.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'date', 'method', 'provider', name='uq_iv_daily_natural')
    )
    op.create_table('jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_by', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_claim', ['status', 'run_after'], unique=False)
        batch_op.create_index('ix_jobs_user_created', ['user_id', 'created_at'], unique=False)

    op.create_table('llm_usage_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('correlation_id', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model_requested', sa.String(length=64), nullable=False),
    sa.Column('model_served', sa.String(length=64), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
    sa.Column('est_cost_usd', sa.Float(), nullable=False),
    sa.Column('pricing_version', sa.String(length=48), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('llm_usage_events', schema=None) as batch_op:
        batch_op.create_index('ix_llm_usage_day', ['day'], unique=False)
        batch_op.create_index('ix_llm_usage_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index('ix_llm_usage_user_day', ['user_id', 'day'], unique=False)

    op.create_table('option_quote_snapshots',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('contract_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('data_status', sa.String(length=16), nullable=False),
    sa.Column('bid', sa.Float(), nullable=True),
    sa.Column('ask', sa.Float(), nullable=True),
    sa.Column('last', sa.Float(), nullable=True),
    sa.Column('volume', sa.Integer(), nullable=True),
    sa.Column('open_interest', sa.Integer(), nullable=True),
    sa.Column('implied_volatility', sa.Float(), nullable=True),
    sa.Column('iv_source', sa.String(length=48), nullable=False),
    sa.Column('underlying_price', sa.Float(), nullable=True),
    sa.Column('batch_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['ingest_batches.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['option_contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contract_id', 'as_of', 'provider', name='uq_quote_snapshot_natural')
    )
    with op.batch_alter_table('option_quote_snapshots', schema=None) as batch_op:
        batch_op.create_index('ix_quote_snapshots_asof', ['as_of'], unique=False)

    op.create_table('positions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('ticker', sa.String(length=16), nullable=False),
    sa.Column('legs', sa.JSON(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name', name='uq_positions_user_name')
    )
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.create_index('ix_positions_user_updated', ['user_id', 'updated_at'], unique=False)

    op.create_table('saved_screens',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.Column('dsl_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name', name='uq_saved_screens_user_name')
    )
    op.create_table('underlying_daily_bars',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('symbol', sa.String(length=16), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('close', sa.Float(), nullable=False),
    sa.Column('adj_close', sa.Float(), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('batch_id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['ingest_batches.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'date', 'provider', name='uq_daily_bar_natural')
    )
    op.create_table('messages',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('conversation_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('context_text', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('served_by', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index('ix_messages_conversation_id_id', ['conversation_id', 'id'], unique=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    # Reviewed: initial OptionsCoach schema (user state, usage ledger, jobs, market data).
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ix_messages_conversation_id_id')

    op.drop_table('messages')
    op.drop_table('underlying_daily_bars')
    op.drop_table('saved_screens')
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_index('ix_positions_user_updated')

    op.drop_table('positions')
    with op.batch_alter_table('option_quote_snapshots', schema=None) as batch_op:
        batch_op.drop_index('ix_quote_snapshots_asof')

    op.drop_table('option_quote_snapshots')
    with op.batch_alter_table('llm_usage_events', schema=None) as batch_op:
        batch_op.drop_index('ix_llm_usage_user_day')
        batch_op.drop_index('ix_llm_usage_user_created')
        batch_op.drop_index('ix_llm_usage_day')

    op.drop_table('llm_usage_events')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_user_created')
        batch_op.drop_index('ix_jobs_claim')

    op.drop_table('jobs')
    op.drop_table('iv_daily')
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_index('ix_conversations_user_updated')

    op.drop_table('conversations')
    op.drop_table('users')
    with op.batch_alter_table('option_contracts', schema=None) as batch_op:
        batch_op.drop_index('ix_option_contracts_underlying_exp')

    op.drop_table('option_contracts')
    op.drop_table('ingest_batches')
    # ### end Alembic commands ###
