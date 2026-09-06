"""Shared auth setup for platform contract tests.

Existing endpoint tests predate enforcement; their fixtures call
signup_owner() so requests carry an owner session cookie.
"""

OWNER_EMAIL = "owner@acme.test"
OWNER_PASSWORD = "correct-horse-1"


async def signup_owner(client, email: str = OWNER_EMAIL):
    """Register the first (owner) user on a fresh app. Asserts success."""
    resp = await client.post(
        "/auth/signup", json={"email": email, "name": "Owner", "password": OWNER_PASSWORD}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "owner"
    return resp.json()
