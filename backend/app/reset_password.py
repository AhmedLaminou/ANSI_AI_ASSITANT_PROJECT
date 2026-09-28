r"""Reset the password of an existing account, from the machine hosting the database.

    .\.venv\Scripts\python.exe -m app.reset_password

For an agent who forgot their password, the normal path is the interface: they file
a request from the sign-in screen, and an administrator resolves it with a temporary
password the agent must replace. This script is the recovery path for when that
cannot work — typically the only administrator locked out of their own account —
and it requires access to the server, which is the right bar for it.

Every session of the account is revoked: a reset usually means the old password is
compromised, and a stolen session must not outlive it.

The password is read with getpass: it is never passed as an argument, so it does not
land in the shell history nor in the process list.
"""

from getpass import getpass

from sqlalchemy import select

from .access import department_label
from .auth import password_hash, revoke_sessions
from .database import SessionLocal, User, initialise_database

MINIMUM_LENGTH = 12


def main() -> None:
    initialise_database()

    identifier = input("Adresse ou identifiant du compte : ").strip()
    with SessionLocal() as db:
        if "@" in identifier:
            user = db.scalar(select(User).where(User.email == identifier.lower()))
        else:
            user = db.scalar(select(User).where(User.username == identifier))
        if user is None:
            # Naming the account is fine here: the operator already has the database.
            raise SystemExit("Aucun compte ne correspond.")

        print(f"Compte trouvé : {user.username} ({user.email or 'sans adresse'}), rôle {user.role}, "
              f"service {department_label(user.department)}, état {user.status}.")
        if user.status != "active" or not user.is_active:
            print("Attention : ce compte n'est pas actif, il ne pourra pas se connecter "
                  "tant que son état n'aura pas été rétabli.")

        password = getpass("Nouveau mot de passe : ")
        if len(password) < MINIMUM_LENGTH:
            raise SystemExit(f"Utilisez au moins {MINIMUM_LENGTH} caractères.")
        if getpass("Confirmer le mot de passe : ") != password:
            raise SystemExit("Les mots de passe ne correspondent pas.")

        user.password_hash = password_hash.hash(password)
        # The operator typed it for themselves in the usual case, so it is not flagged
        # as temporary; for someone else's account, use the interface instead.
        user.must_change_password = False
        revoke_sessions(user)
        db.commit()

    print("Mot de passe remplacé. Toutes les sessions ouvertes de ce compte ont été fermées.")


if __name__ == "__main__":
    main()
