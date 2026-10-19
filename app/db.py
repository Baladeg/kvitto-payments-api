from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Base, Tariff

TARIFFS = [
    {"title": "basic", "price": 990_000},
    {"title": "standard", "price": 1_990_000},
    {"title": "premium", "price": 2_990_000},
]


def make_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
    )


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        statement = insert(Tariff).values(TARIFFS)
        connection.execute(
            statement.on_conflict_do_update(
                constraint="uq_tariffs_title",
                set_={"price": statement.excluded.price},
            )
        )


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
