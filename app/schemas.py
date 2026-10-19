from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

PaymentMethod = Literal["card", "sbp", "installment"]
PaymentStatus = Literal["pending", "succeeded", "failed", "refunded"]
PositiveId = Annotated[int, Field(strict=True, gt=0, le=2_147_483_647)]


class TariffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    price: int


class PaymentCreate(BaseModel):
    tariff_id: PositiveId
    email: EmailStr
    method: PaymentMethod
    installment_months: Annotated[int, Field(strict=True)] | None = None
    promo_code: str | None = None

    @field_validator("promo_code")
    @classmethod
    def validate_promo_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.upper() != "KVITTO10":
            raise ValueError("Unknown promo code")
        return "KVITTO10"

    @model_validator(mode="after")
    def validate_installment(self) -> Self:
        if self.method == "installment":
            if self.installment_months not in (3, 6, 12):
                raise ValueError("Installment requires installment_months: 3, 6 or 12")
        elif self.installment_months is not None:
            raise ValueError("installment_months is only allowed for installment")
        return self


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: PaymentStatus
    tariff_id: int
    amount: int
    discount: int
    method: PaymentMethod
    installment_months: int | None
    schedule: list[int] | None
    email: EmailStr
    created_at: datetime


class BankWebhook(BaseModel):
    payment_id: UUID
    status: PaymentStatus
