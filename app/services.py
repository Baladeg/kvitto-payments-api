from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Payment, Tariff
from app.schemas import PaymentCreate, PaymentStatus

ALLOWED_TRANSITIONS = {
    ("pending", "succeeded"),
    ("pending", "failed"),
    ("succeeded", "refunded"),
}


def create_payment(
    session: Session, data: PaymentCreate, idempotency_key: str | None
) -> tuple[Payment, bool]:
    with session.begin():
        if idempotency_key is not None:
            existing = session.scalar(
                select(Payment).where(Payment.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return existing, False

        tariff = session.get(Tariff, data.tariff_id)
        if tariff is None:
            raise HTTPException(status_code=404, detail="Tariff not found")

        discount = tariff.price // 10 if data.promo_code is not None else 0
        statement = insert(Payment).values(
            tariff_id=tariff.id,
            amount=tariff.price - discount,
            discount=discount,
            method=data.method,
            installment_months=data.installment_months,
            email=str(data.email),
            idempotency_key=idempotency_key,
        )
        statement = statement.on_conflict_do_nothing(
            constraint="uq_payments_idempotency_key"
        ).returning(Payment)
        payment = session.scalar(statement)
        if payment is not None:
            return payment, True

        # At READ COMMITTED this new statement sees the concurrent winner's commit.
        payment = session.scalars(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        ).one()
        return payment, False


def get_payment(session: Session, payment_id: UUID) -> Payment:
    payment = session.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


def change_payment_status(
    session: Session, payment_id: UUID, new_status: PaymentStatus
) -> bool:
    with session.begin():
        payment = session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if (payment.status, new_status) not in ALLOWED_TRANSITIONS:
            return False
        payment.status = new_status
    return True
