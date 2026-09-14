r"""Supprime les conversations plus anciennes que CONVERSATION_RETENTION_DAYS.

A planifier (tache planifiee Windows ou cron) une fois la duree de retention arbitree :

    .\.venv\Scripts\python.exe -m app.purge_conversations

La purge s'execute aussi au demarrage de l'API. Tant que CONVERSATION_RETENTION_DAYS
vaut 0, rien n'est supprime : la duree doit etre decidee, pas subie par defaut.
"""

from .config import get_settings
from .main import purge_expired_conversations


def main() -> None:
    days = get_settings().conversation_retention_days
    if days <= 0:
        print("Retention desactivee (CONVERSATION_RETENTION_DAYS=0). Aucune conversation supprimee.")
        return
    removed = purge_expired_conversations()
    print(f"Retention : {days} jours. Conversations supprimees : {removed}.")


if __name__ == "__main__":
    main()
