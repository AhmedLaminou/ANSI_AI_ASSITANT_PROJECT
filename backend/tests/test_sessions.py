r"""Sessions that can be withdrawn, and passwords the administrator never keeps.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_sessions.py -q

Until 28/09/2026, changing a password left every stolen session valid for up to
eight hours: a signed token cannot be withdrawn once issued. It now carries the
account's `token_version`, compared on every request; incrementing the version is
what revokes.

No Ollama: none of this reaches the model.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import PASSWORD_CHANGE_REQUIRED, password_hash
from app.database import AuditEvent, PasswordResetRequest, SessionLocal, User, initialise_database
from app.main import app


PASSWORD = "SessionTestPassword-2026"
NEW_PASSWORD = "SessionNouveauPassword-2026"


@pytest.fixture
def accounts():
    """An administrator and an agent, both with an address, removed afterwards."""
    initialise_database()
    run = uuid.uuid4().hex[:8]
    admin_email, agent_email = f"adm.{run}@ansi.ne", f"agent.{run}@ansi.ne"
    with SessionLocal() as db:
        db.add_all([
            User(username=f"adm-{run}", email=admin_email, password_hash=password_hash.hash(PASSWORD),
                 role="admin", department="technique", status="active"),
            User(username=f"agent-{run}", email=agent_email, password_hash=password_hash.hash(PASSWORD),
                 role="user", department="rh", status="active"),
        ])
        db.commit()
        ids = [row.id for row in db.scalars(select(User).where(User.email.in_([admin_email, agent_email]))).all()]
    try:
        yield {"admin": admin_email, "agent": agent_email, "ids": ids}
    finally:
        with SessionLocal() as db:
            db.execute(delete(PasswordResetRequest).where(PasswordResetRequest.user_id.in_(ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(ids)))
            db.execute(delete(User).where(User.id.in_(ids)))
            db.commit()


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """The limiter lives in process memory and every TestClient comes from the same
    address, so without this, earlier tests spend the quota of later ones."""
    from app import main

    main._rate_buckets.clear()
    yield
    main._rate_buckets.clear()


def sign_in(client: TestClient, email: str, password: str = PASSWORD) -> int:
    return client.post("/auth/login", json={"username": email, "password": password}).status_code


def agent_of(emails) -> User:
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.email == emails["agent"]))


# --------------------------------------------------------------------------
# Revocation
# --------------------------------------------------------------------------

def test_changing_my_password_closes_my_other_sessions_but_not_this_one(accounts):
    with TestClient(app) as elsewhere, TestClient(app) as here:
        assert sign_in(elsewhere, accounts["agent"]) == 204
        assert sign_in(here, accounts["agent"]) == 204
        assert elsewhere.get("/auth/me").status_code == 200

        changed = here.post("/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        assert changed.status_code == 204

        # The session on the other machine is gone...
        assert elsewhere.get("/auth/me").status_code == 401
        # ...and the one that made the change was re-issued, so it keeps working.
        assert here.get("/auth/me").status_code == 200


def test_a_revoked_session_says_why(accounts):
    with TestClient(app) as elsewhere, TestClient(app) as here:
        sign_in(elsewhere, accounts["agent"])
        sign_in(here, accounts["agent"])
        here.post("/auth/sessions/revoke")
        refused = elsewhere.get("/auth/me")
    assert refused.status_code == 401
    assert "fermée" in refused.json()["detail"]


def test_signing_out_everywhere_keeps_the_current_session(accounts):
    with TestClient(app) as elsewhere, TestClient(app) as here:
        sign_in(elsewhere, accounts["agent"])
        sign_in(here, accounts["agent"])
        assert here.post("/auth/sessions/revoke").status_code == 204
        assert here.get("/auth/me").status_code == 200
        assert elsewhere.get("/auth/me").status_code == 401


def test_an_administrator_closes_every_session_of_an_account(accounts):
    with TestClient(app) as agent, TestClient(app) as admin:
        sign_in(agent, accounts["agent"])
        sign_in(admin, accounts["admin"])
        target = agent_of(accounts).id
        assert admin.post(f"/admin/users/{target}/revoke-sessions").status_code == 204
        assert agent.get("/auth/me").status_code == 401


def test_deactivating_an_account_revokes_its_sessions_for_good(accounts):
    """Reactivating it later must not bring the old sessions back to life."""
    with TestClient(app) as agent, TestClient(app) as admin:
        sign_in(agent, accounts["agent"])
        sign_in(admin, accounts["admin"])
        target = agent_of(accounts).id
        admin.patch(f"/admin/users/{target}", json={"is_active": False})
        admin.patch(f"/admin/users/{target}", json={"is_active": True})
        assert agent.get("/auth/me").status_code == 401


def test_the_token_carries_no_role():
    """Authorisation is read from the database on every request. A role claim that
    nothing reads is a trap for whoever reads it next."""
    import jwt

    from app.auth import create_access_token
    from app.config import get_settings

    token = create_access_token(User(id=1, username="x", password_hash="x", role="admin", token_version=3))
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    assert "role" not in payload
    assert payload["ver"] == 3


# --------------------------------------------------------------------------
# A password chosen by the administrator is temporary by construction
# --------------------------------------------------------------------------

def test_an_administrator_reset_forces_a_change_at_next_sign_in(accounts):
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        target = agent_of(accounts).id
        assert admin.post(f"/admin/users/{target}/password", json={"password": "Provisoire-2026-ABCD"}).status_code == 204

    with TestClient(app) as agent:
        assert sign_in(agent, accounts["agent"], "Provisoire-2026-ABCD") == 204
        me = agent.get("/auth/me")
        assert me.status_code == 200 and me.json()["must_change_password"] is True


def test_until_it_is_changed_everything_else_is_closed(accounts):
    """The gate is checked once, in get_current_user, not remembered route by route."""
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        admin.post(f"/admin/users/{agent_of(accounts).id}/password", json={"password": "Provisoire-2026-ABCD"})

    with TestClient(app) as agent:
        sign_in(agent, accounts["agent"], "Provisoire-2026-ABCD")
        for path in ("/documents", "/conversations", "/auth/profile"):
            blocked = agent.get(path)
            assert blocked.status_code == 403, path
            assert blocked.json()["detail"] == PASSWORD_CHANGE_REQUIRED
        blocked_chat = agent.post("/chat", json={"message": "Bonjour"})
        assert blocked_chat.status_code == 403


def test_choosing_my_own_password_lifts_the_gate(accounts):
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        admin.post(f"/admin/users/{agent_of(accounts).id}/password", json={"password": "Provisoire-2026-ABCD"})

    with TestClient(app) as agent:
        sign_in(agent, accounts["agent"], "Provisoire-2026-ABCD")
        assert agent.post("/auth/password", json={
            "current_password": "Provisoire-2026-ABCD", "new_password": NEW_PASSWORD,
        }).status_code == 204
        assert agent.get("/auth/me").json()["must_change_password"] is False
        assert agent.get("/documents").status_code == 200


def test_an_account_created_by_the_administrator_must_choose_its_password(accounts):
    run = uuid.uuid4().hex[:8]
    email = f"cree.{run}@ansi.ne"
    try:
        with TestClient(app) as admin:
            sign_in(admin, accounts["admin"])
            created = admin.post("/admin/users", json={
                "email": email, "password": "Initial-2026-ABCD", "role": "user", "department": "rh",
            })
            assert created.status_code == 201, created.text
            assert created.json()["must_change_password"] is True
            # Derived from the address, readable in the audit trail.
            assert created.json()["username"].startswith("cree.")
    finally:
        with SessionLocal() as db:
            account = db.scalar(select(User).where(User.email == email))
            if account:
                db.execute(delete(AuditEvent).where(AuditEvent.actor_id == account.id))
                db.execute(delete(User).where(User.id == account.id))
                db.commit()


def test_an_address_already_in_use_is_refused_at_creation(accounts):
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        refused = admin.post("/admin/users", json={
            "email": accounts["agent"], "password": "Initial-2026-ABCD", "role": "user",
        })
    assert refused.status_code == 409


def test_an_administrator_records_an_address_on_an_older_account(accounts):
    run = uuid.uuid4().hex[:8]
    username = f"legacy-{run}"
    with SessionLocal() as db:
        db.add(User(username=username, email=None, password_hash=password_hash.hash(PASSWORD),
                    role="user", department="rh", status="active"))
        db.commit()
        legacy_id = db.scalar(select(User.id).where(User.username == username))
    try:
        with TestClient(app) as admin:
            sign_in(admin, accounts["admin"])
            updated = admin.patch(f"/admin/users/{legacy_id}", json={"email": f"legacy.{run}@ansi.ne"})
            assert updated.status_code == 200
            assert updated.json()["email"] == f"legacy.{run}@ansi.ne"
            taken = admin.patch(f"/admin/users/{legacy_id}", json={"email": accounts["agent"]})
            assert taken.status_code == 409
    finally:
        with SessionLocal() as db:
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id == legacy_id))
            db.execute(delete(User).where(User.id == legacy_id))
            db.commit()


# --------------------------------------------------------------------------
# « Mot de passe oublié » is a request to the administrator
# --------------------------------------------------------------------------

def test_the_request_answers_the_same_whether_the_address_exists_or_not(accounts):
    """Otherwise the form becomes an oracle for who works at the agency."""
    with TestClient(app) as client:
        known = client.post("/auth/password-reset-request", json={"email": accounts["agent"]})
        unknown = client.post("/auth/password-reset-request",
                              json={"email": f"personne.{uuid.uuid4().hex[:8]}@ansi.ne"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_a_request_reaches_the_administrator_queue(accounts):
    with TestClient(app) as client:
        client.post("/auth/password-reset-request", json={"email": accounts["agent"]})
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        queue = admin.get("/admin/password-resets").json()
    assert any(entry["email"] == accounts["agent"] for entry in queue)


def test_repeated_requests_do_not_flood_the_queue(accounts):
    with TestClient(app) as client:
        for _ in range(3):
            client.post("/auth/password-reset-request", json={"email": accounts["agent"]})
    with SessionLocal() as db:
        pending = db.scalars(
            select(PasswordResetRequest)
            .where(PasswordResetRequest.user_id == agent_of(accounts).id)
            .where(PasswordResetRequest.status == "pending")
        ).all()
    assert len(pending) == 1


def test_the_request_itself_changes_nothing(accounts):
    """It files a request; it never touches the password. Otherwise anyone who knew an
    address could lock its owner out."""
    with TestClient(app) as client:
        client.post("/auth/password-reset-request", json={"email": accounts["agent"]})
    with TestClient(app) as agent:
        assert sign_in(agent, accounts["agent"]) == 204


def test_resolving_sets_a_temporary_password_and_closes_the_request(accounts):
    with TestClient(app) as client:
        client.post("/auth/password-reset-request", json={"email": accounts["agent"]})
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        entry = next(e for e in admin.get("/admin/password-resets").json() if e["email"] == accounts["agent"])
        assert admin.post(f"/admin/password-resets/{entry['id']}/resolve",
                          json={"password": "Provisoire-2026-WXYZ"}).status_code == 204
        assert all(e["id"] != entry["id"] for e in admin.get("/admin/password-resets").json())
        # Resolving twice is a 404, not a second silent reset.
        assert admin.post(f"/admin/password-resets/{entry['id']}/resolve",
                          json={"password": "Autre-2026-WXYZ"}).status_code == 404
    with TestClient(app) as agent:
        assert sign_in(agent, accounts["agent"], "Provisoire-2026-WXYZ") == 204
        assert agent.get("/auth/me").json()["must_change_password"] is True


def test_a_request_can_be_dismissed_without_touching_the_account(accounts):
    with TestClient(app) as client:
        client.post("/auth/password-reset-request", json={"email": accounts["agent"]})
    with TestClient(app) as admin:
        sign_in(admin, accounts["admin"])
        entry = next(e for e in admin.get("/admin/password-resets").json() if e["email"] == accounts["agent"])
        assert admin.post(f"/admin/password-resets/{entry['id']}/dismiss").status_code == 204
    with TestClient(app) as agent:
        assert sign_in(agent, accounts["agent"]) == 204


def test_the_request_is_rate_limited(accounts):
    """The only unauthenticated write besides registration: it must not be floodable."""
    from app.config import get_settings

    limit = get_settings().registration_rate_limit_per_hour
    if limit <= 0:
        pytest.skip("limitation désactivée dans cette configuration")
    with TestClient(app) as client:
        statuses = [
            client.post("/auth/password-reset-request",
                        json={"email": f"x{i}.{uuid.uuid4().hex[:6]}@ansi.ne"}).status_code
            for i in range(limit + 1)
        ]
    assert statuses[:limit] == [202] * limit
    assert statuses[-1] == 429


def test_an_ordinary_account_cannot_see_the_reset_queue(accounts):
    with TestClient(app) as agent:
        sign_in(agent, accounts["agent"])
        assert agent.get("/admin/password-resets").status_code == 403
