import base64

from app.schemas import MAX_PHOTO_BYTES

BASE = "/api/v1/contacts"

# 1x1 transparent PNG — a real, decodable image.
TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def test_create_with_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": TINY_PNG})
    assert response.status_code == 201
    assert response.json()["photo"] == TINY_PNG


def test_photo_defaults_to_null(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    assert response.json()["photo"] is None


def test_photo_rejects_plain_url(client, payload):
    response = client.post(BASE, json={**payload, "photo": "https://example.com/ada.png"})
    assert response.status_code == 422


def test_photo_rejects_non_image_media_type(client, payload):
    text_data_url = "data:text/plain;base64," + base64.b64encode(b"not an image").decode()
    response = client.post(BASE, json={**payload, "photo": text_data_url})
    assert response.status_code == 422


def test_photo_rejects_malformed_base64(client, payload):
    response = client.post(BASE, json={**payload, "photo": "data:image/png;base64,not base64!!"})
    assert response.status_code == 422


def test_photo_rejects_oversized_image(client, payload):
    too_big = "data:image/png;base64," + base64.b64encode(b"\0" * (MAX_PHOTO_BYTES + 1)).decode()
    response = client.post(BASE, json={**payload, "photo": too_big})
    assert response.status_code == 422


def test_patch_sets_and_clears_photo(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]

    updated = client.patch(f"{BASE}/{contact_id}", json={"photo": TINY_PNG})
    assert updated.status_code == 200
    assert updated.json()["photo"] == TINY_PNG

    cleared = client.patch(f"{BASE}/{contact_id}", json={"photo": None})
    assert cleared.status_code == 200
    assert cleared.json()["photo"] is None


def test_put_without_photo_clears_it(client, payload):
    # PUT is a full replacement, so omitting `photo` removes it — clients that
    # want to keep the photo must send it back (the edit form does).
    contact_id = client.post(BASE, json={**payload, "photo": TINY_PNG}).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["photo"] is None
