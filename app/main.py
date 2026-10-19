import hmac
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from math import isfinite
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_database_url, get_webhook_secret
from app.db import get_session, initialize_database, make_engine
from app.models import Tariff
from app.schemas import BankWebhook, PaymentCreate, PaymentRead, TariffRead
from app.services import change_payment_status, create_payment, get_payment

SessionDependency = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


class SignedBankWebhookRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def signed_handler(request: Request) -> Response:
            # Routing has already selected this endpoint; JSON parsing has not begun.
            signatures = request.headers.getlist("X-Signature")
            signature = signatures[0] if len(signatures) == 1 else ""
            if re.fullmatch(r"[0-9a-fA-F]{64}", signature) is None:
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            expected = hmac.digest(
                request.app.state.webhook_secret, await request.body(), "sha256"
            )
            if not hmac.compare_digest(bytes.fromhex(signature), expected):
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            return await handler(request)

        return signed_handler


async def request_validation_error_handler(
    _request: Request, error: RequestValidationError
) -> Response:
    # Preserve FastAPI's error structure; only non-finite diagnostic values change.
    errors = jsonable_encoder(
        error.errors(),
        custom_encoder={float: lambda value: value if isfinite(value) else str(value)},
    )
    return Response(
        # ASCII escapes also make unpaired surrogate characters safe on the wire.
        content=json.dumps(
            {"detail": errors}, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ),
        status_code=422,
        media_type="application/json",
    )


def create_app(database_url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.webhook_secret = get_webhook_secret()
        engine = make_engine(database_url or get_database_url())
        try:
            initialize_database(engine)
            application.state.engine = engine
            application.state.session_factory = sessionmaker(
                engine, expire_on_commit=False
            )
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="Kvitto Payments", lifespan=lifespan)
    application.add_exception_handler(
        RequestValidationError, request_validation_error_handler
    )

    @application.get("/tariffs", response_model=list[TariffRead])
    def list_tariffs(session: SessionDependency):
        return session.scalars(select(Tariff).order_by(Tariff.id)).all()

    @application.post("/payments", response_model=PaymentRead, status_code=201)
    def post_payment(
        data: PaymentCreate,
        response: Response,
        session: SessionDependency,
        idempotency_key: IdempotencyKey = None,
    ):
        payment, created = create_payment(session, data, idempotency_key)
        response.status_code = 201 if created else 200
        return PaymentRead.model_validate(payment)

    @application.get("/payments/{id}", response_model=PaymentRead)
    def read_payment(id: UUID, session: SessionDependency):
        return PaymentRead.model_validate(get_payment(session, id))

    bank_webhooks = APIRouter(route_class=SignedBankWebhookRoute)

    @bank_webhooks.post("/webhooks/bank")
    def bank_webhook(data: BankWebhook, session: SessionDependency):
        if not change_payment_status(session, data.payment_id, data.status):
            return JSONResponse(
                status_code=409, content={"error": "invalid_transition"}
            )
        return {"result": "ok"}

    application.include_router(bank_webhooks)
    return application


app = create_app()
