from app.db import initialize_database


def test_tariffs_and_repeated_initialization(client, engine):
    before = client.get("/tariffs")
    assert before.status_code == 200
    assert [(item["title"], item["price"]) for item in before.json()] == [
        ("basic", 990_000),
        ("standard", 1_990_000),
        ("premium", 2_990_000),
    ]
    assert all(set(item) == {"id", "title", "price"} for item in before.json())

    initialize_database(engine)
    initialize_database(engine)
    assert client.get("/tariffs").json() == before.json()
