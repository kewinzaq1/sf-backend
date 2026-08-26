from sqlalchemy import func, select

from app.database import SessionLocal
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


def test_deleting_a_contact_deletes_its_addresses(client, payload):
    contact_id = client.post(BASE, json={**payload, "addresses": [HOME, WORK]}).json()["id"]
    assert address_count() == 2

    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert address_count() == 0
