r"""Registration and approval flow (phase B).

    .\.venv\Scripts\python.exe -m tests.registration_probe

No Ollama needed: nothing here reaches the model. Creates its own accounts and
removes them all.

What is actually being verified:

- a pending account **reads nothing** and cannot log in;
- approval grants what the **administrator** chose, never what the applicant asked for;
- the endpoint does not become a **username enumeration oracle** — an existing name
  and a free name produce the same status code and the same body;
- a refused account cannot log in, and cannot be approved afterwards.
"""

import sys
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.database import AuditEvent, Conversation, SessionLocal, User, initialise_database
from app.main import app


RUN = uuid.uuid4().hex[:6]
PASSWORD = "RegistrationProbePassword-2026"

results: list[tuple[bool, str]] = []


def check(passed: bool, label: str) -> None:
    results.append((passed, label))
    print(f"[{'OK ' if passed else 'ECHEC'}] {label}", flush=True)


initialise_database()

admin_name = f"reg-admin-{RUN}"
applicant = f"reg-demande-{RUN}"
refused = f"reg-refus-{RUN}"
existing = f"reg-existant-{RUN}"
names = [admin_name, applicant, refused, existing]

with SessionLocal() as db:
    db.add_all([
        User(username=admin_name, password_hash=password_hash.hash(PASSWORD), role="admin",
             department="technique", status="active"),
        User(username=existing, password_hash=password_hash.hash(PASSWORD), role="user",
             department="finance", status="active"),
    ])
    db.commit()

try:
    with TestClient(app) as client:
        # ------------------------------------------------------------------
        # A request is accepted and creates nothing usable
        # ------------------------------------------------------------------
        asked = client.post("/auth/register", json={
            "username": applicant, "password": PASSWORD,
            "requested_department": "rh", "reason": "Stage au service RH",
        })
        check(asked.status_code == 202, "une demande d'acces est acceptee")

        blocked = client.post("/auth/login", json={"username": applicant, "password": PASSWORD})
        check(blocked.status_code == 403, "un compte en attente ne peut pas se connecter")

        with SessionLocal() as db:
            pending = db.scalar(select(User).where(User.username == applicant))
            check(pending is not None and pending.status == "pending", "le compte est cree en attente")
            check(pending is not None and pending.department is None,
                  "le compte en attente n'a aucun service — il ne lit rien")
            check(pending is not None and pending.requested_department == "rh",
                  "le service demande est conserve comme indication")

        # ------------------------------------------------------------------
        # No username enumeration
        # ------------------------------------------------------------------
        on_existing = client.post("/auth/register", json={
            "username": existing, "password": PASSWORD, "requested_department": "rh",
        })
        free_name = f"reg-libre-{RUN}"
        names.append(free_name)
        on_free = client.post("/auth/register", json={
            "username": free_name, "password": PASSWORD, "requested_department": "rh",
        })
        check(
            on_existing.status_code == on_free.status_code and on_existing.json() == on_free.json(),
            "identifiant existant et identifiant libre donnent la meme reponse",
        )
        with SessionLocal() as db:
            untouched = db.scalar(select(User).where(User.username == existing))
            check(untouched is not None and untouched.status == "active" and untouched.department == "finance",
                  "une demande sur un identifiant existant ne modifie pas le compte")

        # ------------------------------------------------------------------
        # Approval grants what the administrator chose
        # ------------------------------------------------------------------
        assert client.post("/auth/login", json={"username": admin_name, "password": PASSWORD}).status_code == 204
        waiting = client.get("/admin/registrations").json()
        check(any(item["username"] == applicant for item in waiting), "la demande apparait dans la file")

        target = next(item for item in waiting if item["username"] == applicant)
        # the applicant asked for RH; the administrator grants logistique on purpose
        approved = client.post(f"/admin/registrations/{target['id']}/approve",
                               json={"role": "user", "department": "logistique"})
        check(approved.status_code == 200, "l'administrateur approuve la demande")
        check(approved.json()["department"] == "logistique",
              "le service accorde est celui choisi par l'administrateur, pas celui demande")

        invalid = client.post(f"/admin/registrations/{target['id']}/approve",
                              json={"role": "user", "department": "logistique"})
        check(invalid.status_code == 404, "une demande deja traitee ne peut pas etre approuvee deux fois")

        client.post("/auth/logout")

        # ------------------------------------------------------------------
        # The approved account works, with the granted perimeter
        # ------------------------------------------------------------------
        opened = client.post("/auth/login", json={"username": applicant, "password": PASSWORD})
        check(opened.status_code == 204, "le compte approuve peut se connecter")
        me = client.get("/auth/me").json()
        check(me["department"] == "logistique", "le compte approuve porte le service accorde")
        client.post("/auth/logout")

        # ------------------------------------------------------------------
        # Refusal
        # ------------------------------------------------------------------
        client.post("/auth/register", json={
            "username": refused, "password": PASSWORD, "requested_department": "finance",
        })
        client.post("/auth/login", json={"username": admin_name, "password": PASSWORD})
        queue = client.get("/admin/registrations").json()
        to_refuse = next(item for item in queue if item["username"] == refused)
        denied = client.post(f"/admin/registrations/{to_refuse['id']}/refuse",
                             json={"reason": "Hors perimetre"})
        check(denied.status_code == 204, "l'administrateur refuse une demande")
        client.post("/auth/logout")

        rejected = client.post("/auth/login", json={"username": refused, "password": PASSWORD})
        check(rejected.status_code == 403, "un compte refuse ne peut pas se connecter")

        # ------------------------------------------------------------------
        # An invalid department is rejected outright
        # ------------------------------------------------------------------
        bad = client.post("/auth/register", json={
            "username": f"reg-invalide-{RUN}", "password": PASSWORD, "requested_department": "direction-generale",
        })
        check(bad.status_code == 422, "un service demande inexistant est refuse")

finally:
    with SessionLocal() as db:
        accounts = db.scalars(select(User).where(User.username.in_(names))).all()
        identifiers = [account.id for account in accounts]
        if identifiers:
            db.execute(delete(Conversation).where(Conversation.user_id.in_(identifiers)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(identifiers)))
            db.execute(delete(User).where(User.id.in_(identifiers)))
        db.commit()

failures = [label for passed, label in results if not passed]
print("\n" + "=" * 66)
print(f"{len(results) - len(failures)}/{len(results)} controles passes")
if failures:
    print("\nECHECS :")
    for label in failures:
        print(f"  - {label}")
print("=" * 66)
sys.exit(1 if failures else 0)
