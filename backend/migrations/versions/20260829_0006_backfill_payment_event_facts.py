"""Backfill normalized facts from existing sanitized webhook history.

Revision ID: 20260829_0006
Revises: 20260829_0005
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260829_0006"
down_revision: str | None = "20260829_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one deterministic fact for every valid historical payment event."""

    op.execute(
        """
        INSERT INTO payment_event_facts (
            id,
            webhook_event_id,
            merchant_id,
            razorpay_event_id,
            razorpay_account_id,
            event_type,
            payment_id,
            order_id,
            amount_subunits,
            currency,
            status,
            method,
            bank,
            wallet,
            error_code,
            error_source,
            error_step,
            error_reason,
            payment_created_at,
            provider_event_at
        )
        SELECT
            CAST(md5(CAST(id AS text) || '-payment-event-fact') AS uuid),
            id,
            merchant_id,
            razorpay_event_id,
            razorpay_account_id,
            event_type,
            payload #>> '{payload,payment,entity,id}',
            NULLIF(payload #>> '{payload,payment,entity,order_id}', ''),
            CAST(payload #>> '{payload,payment,entity,amount}' AS integer),
            payload #>> '{payload,payment,entity,currency}',
            CASE event_type
                WHEN 'payment.failed' THEN 'failed'
                WHEN 'payment.authorized' THEN 'authorized'
                WHEN 'payment.captured' THEN 'captured'
            END,
            CASE
                WHEN payload #>> '{payload,payment,entity,method}' IN (
                    'card', 'upi', 'netbanking', 'wallet', 'emi',
                    'cardless_emi', 'paylater'
                ) THEN payload #>> '{payload,payment,entity,method}'
                ELSE 'other'
            END,
            NULLIF(payload #>> '{payload,payment,entity,bank}', ''),
            NULLIF(payload #>> '{payload,payment,entity,wallet}', ''),
            NULLIF(payload #>> '{payload,payment,entity,error_code}', ''),
            CASE
                WHEN payload #>> '{payload,payment,entity,error_source}' IN (
                    'customer', 'business', 'bank', 'gateway', 'internal'
                ) THEN payload #>> '{payload,payment,entity,error_source}'
                WHEN payload #>> '{payload,payment,entity,error_source}' IS NOT NULL
                    THEN 'unknown'
                ELSE NULL
            END,
            NULLIF(payload #>> '{payload,payment,entity,error_step}', ''),
            NULLIF(payload #>> '{payload,payment,entity,error_reason}', ''),
            to_timestamp(
                CAST(payload #>> '{payload,payment,entity,created_at}' AS double precision)
            ),
            provider_event_at
        FROM webhook_events
        WHERE event_type IN (
            'payment.failed', 'payment.authorized', 'payment.captured'
        )
          AND payload #>> '{payload,payment,entity,id}' IS NOT NULL
          AND payload #>> '{payload,payment,entity,amount}' IS NOT NULL
          AND payload #>> '{payload,payment,entity,currency}' IS NOT NULL
          AND payload #>> '{payload,payment,entity,created_at}' IS NOT NULL
        ON CONFLICT (webhook_event_id) DO NOTHING
        """
    )


def downgrade() -> None:
    """Remove only facts created by this deterministic backfill."""

    op.execute(
        """
        DELETE FROM payment_event_facts AS fact
        USING webhook_events AS event
        WHERE fact.webhook_event_id = event.id
          AND fact.id = CAST(
              md5(CAST(event.id AS text) || '-payment-event-fact') AS uuid
          )
        """
    )
