import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, engine, ensure_addresses_migrated
from app.models import Address

BASE = "/api/v1/contacts"

HOME = {"type": "home", "city": "San Francisco", "state": "CA", "country": "USA"}
WORK = {
    "type": "work",
    "street": "1 Market St, Suite 400",
    "city": "San Francisco",
    "state": "CA",
    "postal_code": "94105",
    "country": "USA",
}


def address_count() -> int:
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(Address)).scalar_one()


def test_create_contact_with_multiple_addresses(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [HOME, WORK]})
    assert response.status_code == 201

    addresses = response.json()["addresses"]
    assert [a["type"] for a in addresses] == ["home", "work"]
    assert all(a["id"] > 0 for a in addresses)
    assert addresses[1]["street"] == "1 Market St, Suite 400"


def test_addresses_default_to_empty_list(client, payload):
    response = client.post(BASE, json={**payload, "addresses": []})
    assert response.status_code == 201
    assert response.json()["addresses"] == []


def test_address_type_defaults_to_home(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{"city": "London"}]})
    assert response.status_code == 201
    assert response.json()["addresses"][0]["type"] == "home"


def test_address_rejects_unknown_type(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{**HOME, "type": "vacation"}]})
    assert response.status_code == 422


def test_address_list_is_capped(client, payload):
    too_many = [HOME] * 21
    response = client.post(BASE, json={**payload, "addresses": too_many})
    assert response.status_code == 422


def test_put_replaces_the_whole_address_set(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]

    response = client.put(
        f"{BASE}/{contact_id}",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "addresses": [{"type": "other", "city": "London", "country": "UK"}],
        },
    )
    assert response.status_code == 200
    addresses = response.json()["addresses"]
    assert len(addresses) == 1
    assert addresses[0]["type"] == "other"
    # The replaced rows are deleted, not left dangling.
    assert address_count() == 1


def test_patch_without_addresses_keeps_them(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]

    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    assert len(response.json()["addresses"]) == 2


def test_patch_with_addresses_replaces_them(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME]}).json()["id"]

    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": [WORK, HOME]})
    assert response.status_code == 200
    assert [a["type"] for a in response.json()["addresses"]] == ["work", "home"]
    assert address_count() == 2


def test_patch_null_clears_addresses(client, payload):
    # A present field always replaces the stored set: `null` and `[]` are
    # both "no addresses", never a silent no-op.
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]

    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": None})
    assert response.status_code == 200
    assert response.json()["addresses"] == []
    assert address_count() == 0


def test_address_only_patch_bumps_updated_at(client, payload):
    created = client.post(BASE, json={**payload, "addresses": [HOME]}).json()

    response = client.patch(f"{BASE}/{created['id']}", json={"addresses": [WORK]})
    assert response.status_code == 200
    assert response.json()["updated_at"] > created["updated_at"]


def test_legacy_flat_address_fields_are_rejected(client, payload):
    # The flat pre-relation fields are gone from the contract; accepting and
    # silently dropping them would corrupt old clients' expectations.
    response = client.post(BASE, json={**payload, "city": "San Francisco"})
    assert response.status_code == 422


def test_address_type_is_checked_by_the_database(client, payload):
    # The enum is enforced in the schema AND as a CHECK constraint, so even
    # writes that bypass the API cannot store an unknown type.
    contact_id = client.post(BASE, json=payload).json()["id"]
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO addresses (contact_id, type) VALUES (:cid, 'vacation')"),
                {"cid": contact_id},
            )


def test_legacy_flat_columns_migrate_into_addresses(tmp_path):
    # A database from before the one-to-many model keeps its flat columns;
    # startup must copy that data into the addresses table exactly once.
    legacy = create_engine(f"sqlite+pysqlite:///{tmp_path}/legacy.db")
    with legacy.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE contacts (id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, "
                "email TEXT, address TEXT, city TEXT, state TEXT, postal_code TEXT, country TEXT)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE addresses (id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL, "
                "type VARCHAR(10) NOT NULL, street TEXT, city TEXT, state TEXT, postal_code TEXT, country TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO contacts (first_name, last_name, email, address, city, state, country) "
                "VALUES ('Ada', 'Lovelace', 'ada@example.com', '1 Market St', 'San Francisco', 'CA', 'USA')"
            )
        )
        connection.execute(
            text("INSERT INTO contacts (first_name, last_name, email) VALUES ('Grace', 'Hopper', 'grace@example.com')")
        )

    ensure_addresses_migrated(legacy)
    ensure_addresses_migrated(legacy)  # idempotent

    with legacy.connect() as connection:
        rows = connection.execute(
            text("SELECT contact_id, type, street, city FROM addresses ORDER BY id")
        ).all()
    assert rows == [(1, "home", "1 Market St", "San Francisco")]


def test_deleting_a_contact_deletes_its_addresses(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    assert address_count() == 2

    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert address_count() == 0
