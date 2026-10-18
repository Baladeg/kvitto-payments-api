from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (
        UniqueConstraint("title", name="uq_tariffs_title"),
        CheckConstraint("price > 0", name="ck_tariffs_positive_price"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(String(32))
    price: Mapped[int] = mapped_column(BigInteger)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="ck_payments_status",
        ),
        CheckConstraint(
            "method IN ('card', 'sbp', 'installment')", name="ck_payments_method"
        ),
        CheckConstraint("amount > 0", name="ck_payments_positive_amount"),
        CheckConstraint("discount >= 0", name="ck_payments_nonnegative_discount"),
        CheckConstraint(
            "(method = 'installment' AND installment_months IS NOT NULL "
            "AND installment_months IN (3, 6, 12)) OR "
            "(method IN ('card', 'sbp') AND installment_months IS NULL)",
            name="ck_payments_installment_months",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 255",
            name="ck_payments_idempotency_key_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    tariff_id: Mapped[int] = mapped_column(
        ForeignKey("tariffs.id", ondelete="RESTRICT")
    )
    amount: Mapped[int] = mapped_column(BigInteger)
    discount: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(16))
    installment_months: Mapped[int | None] = mapped_column(SmallInteger)
    email: Mapped[str] = mapped_column(String(320))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def schedule(self) -> list[int] | None:
        if self.installment_months is None:
            return None
        base, remainder = divmod(self.amount, self.installment_months)
        return [base + 1] * remainder + [base] * (
            self.installment_months - remainder
        )
