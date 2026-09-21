# Déploiement sur les serveurs de l'ANSI

*Procédure de mise en production, du poste de développement aux serveurs de l'agence.*
*Rédigé le 21 septembre 2026.*

Ce document est un **mode opératoire**, pas une discussion d'architecture — celle-ci est au
[§5.7 de `ARCHITECTURE_TECHNIQUE.md`](../docs/ARCHITECTURE_TECHNIQUE.md). Il suppose un serveur Linux
sur le réseau interne ANSI, sans accès Internet.

> **Avertissement en tête, parce qu'il est bloquant.** Deux décisions doivent être prises **avant**
> d'indexer le moindre document réel : la **durée de rétention** des conversations, et la question de
> savoir si les **dossiers individuels** peuvent être indexés. Ce ne sont pas des paramètres
> techniques. Voir la section 9.

---

## 1. Ce qui change entre le portable et le serveur

| | Poste de développement | Serveur ANSI |
|---|---|---|
| Modèle | `qwen3:4b` quantifié, sur processeur | modèle 7B–8B sur **GPU** |
| Latence | 30 à 100 s par réponse | quelques secondes attendues |
| Base | SQLite, fichier local | **PostgreSQL + pgvector**, sauvegardée |
| Frontend | `npm run dev` | fichiers compilés servis par **nginx** |
| Backend | `uvicorn --reload` | service **systemd**, plusieurs processus |
| Accès | `localhost` | **HTTPS** sur le réseau interne |
| Comptes | créés à la main | même chose, puis SSO/LDAP plus tard |

Rien dans le code ne dépend de Windows. Les seuls points d'attention sont les chemins (`TESSERACT_CMD`)
et le fait que `.exe` disparaît.

---

## 2. Dimensionnement

### Le serveur d'inférence

C'est le seul poste où le matériel décide de la qualité perçue.

| | Minimum utilisable | Recommandé |
|---|---|---|
| GPU | 1 × 16 Go VRAM | 1 × 24 Go VRAM (RTX 4090, L4, A10) |
| Modèle | 7B quantifié Q4 | 7B–8B en `fp16` ou Q8 |
| RAM | 32 Go | 64 Go |
| Disque | 100 Go SSD | 250 Go SSD |

Sans GPU, comptez les latences mesurées sur le portable — 30 à 100 secondes par réponse — ce qui
n'est pas utilisable pour un usage quotidien.

**Combien d'agents en parallèle ?** Un GPU de 24 Go sert confortablement **5 à 10 questions
simultanées** avec un modèle 7B. Au-delà, il faut soit un second GPU, soit une file d'attente. Ce
chiffre n'a **pas été mesuré** : c'est une estimation à valider en charge (voir section 10).

### Le serveur applicatif et la base

Modestes : 4 cœurs, 8 Go de RAM, 100 Go de disque suffisent. Le volume des documents domine —
comptez environ **3 à 5 fois la taille du corpus** pour les fichiers, les extraits et les vecteurs.

Les deux rôles peuvent cohabiter sur une seule machine si elle a le GPU. Les séparer facilite les
sauvegardes et permet de redémarrer l'API sans recharger le modèle.

---

## 3. Le transfert hors ligne

C'est la particularité du projet : **la cible n'a pas accès à Internet**. Tout se prépare sur une
machine connectée, se transporte, puis s'installe.

### 3.1 Préparer le colis (machine connectée, même OS que la cible)

```bash
mkdir -p colis-ansi && cd colis-ansi

# 1. Le code
git clone https://github.com/AhmedLaminou/ANSI_AI_ASSITANT_PROJECT.git app
rm -rf app/.git

# 2. Les dépendances Python, en roues précompilées
python3.11 -m pip download -r app/backend/requirements.txt -d wheels

# 3. Le frontend compilé (le serveur n'a pas besoin de Node)
cd app/frontend && npm ci && npm run build && cd ../..
# le résultat est dans app/frontend/dist/

# 4. Les modèles Ollama
#    Sur la machine connectée : ollama pull <modèle>, puis copier le magasin.
ollama pull qwen3:4b
ollama pull embeddinggemma
tar czf modeles-ollama.tar.gz -C ~/.ollama models

# 5. Les paquets système (Debian/Ubuntu)
mkdir -p paquets && cd paquets
apt-get download tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
                 postgresql-16 postgresql-16-pgvector nginx python3.11 python3.11-venv
cd ..

# 6. Le binaire Ollama
curl -L https://ollama.com/download/ollama-linux-amd64.tgz -o ollama.tgz
```

**Vérifiez l'intégrité avant de transporter** — c'est ce qui rend le colis auditable :

```bash
find . -type f -exec sha256sum {} \; | sort -k2 > MANIFESTE.sha256
```

### 3.2 Transporter

Support amovible contrôlé, selon la procédure de l'ANSI. Sur la cible, avant toute installation :

```bash
sha256sum -c MANIFESTE.sha256
```

### 3.3 Ce qu'il faut refaire à chaque mise à jour

Les étapes 1, 2 et 3 seulement. Les modèles et les paquets système ne changent presque jamais : un
colis de mise à jour pèse quelques dizaines de mégaoctets, contre plusieurs gigaoctets au premier
déploiement.

---

## 4. Installation

### 4.1 Paquets système

```bash
sudo dpkg -i paquets/*.deb || sudo apt-get -f install
sudo tar -C /usr -xzf ollama.tgz
sudo useradd -r -s /bin/false -m -d /usr/share/ollama ollama
sudo mkdir -p /usr/share/ollama/.ollama
sudo tar xzf modeles-ollama.tar.gz -C /usr/share/ollama/.ollama
sudo chown -R ollama:ollama /usr/share/ollama
```

### 4.2 L'application

```bash
sudo useradd -r -s /bin/false -m -d /opt/ansi-assistant ansi
sudo cp -r app /opt/ansi-assistant/current
sudo chown -R ansi:ansi /opt/ansi-assistant

sudo -u ansi python3.11 -m venv /opt/ansi-assistant/current/backend/.venv
sudo -u ansi /opt/ansi-assistant/current/backend/.venv/bin/python -m pip install \
     --no-index --find-links=/chemin/vers/colis-ansi/wheels \
     -r /opt/ansi-assistant/current/backend/requirements.txt
```

`--no-index` est important : il **garantit** qu'aucune dépendance n'est cherchée en ligne. Si une
roue manque, l'installation échoue franchement au lieu de tenter une connexion sortante.

### 4.3 PostgreSQL et pgvector

```bash
sudo -u postgres psql <<'SQL'
CREATE USER ansi WITH PASSWORD 'REMPLACER_PAR_UN_SECRET_FORT';
CREATE DATABASE ansi_ai OWNER ansi;
\c ansi_ai
CREATE EXTENSION IF NOT EXISTS vector;
SQL
```

L'application crée ses tables et son index HNSW au premier démarrage. Aucune migration manuelle.

---

## 5. Configuration

`/opt/ansi-assistant/current/.env`, **lisible par le seul compte de service** :

```ini
APP_ENV=production
FRONTEND_ORIGIN=https://assistant.ansi.ne
COOKIE_SECURE=true

# Une valeur aléatoire longue, différente de celle de développement.
# Générer avec : python -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=...

DATABASE_URL=postgresql+psycopg://ansi:LE_SECRET@127.0.0.1:5432/ansi_ai

OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_CHAT_MODEL=qwen3:4b
OLLAMA_EMBEDDING_MODEL=embeddinggemma
OLLAMA_CHAT_REASONING=true

CHAT_RATE_LIMIT_PER_MINUTE=12
LOGIN_RATE_LIMIT_PER_MINUTE=5
REGISTRATION_RATE_LIMIT_PER_HOUR=5

# ⚠️ 0 conserve indéfiniment. À renseigner avant toute donnée réelle (section 9).
CONVERSATION_RETENTION_DAYS=0

TESSERACT_CMD=/usr/bin/tesseract
OCR_LANGUAGES=fra+eng
OCR_DPI=300
OCR_MAX_PAGES=40

MAX_RETRIEVAL_ATTEMPTS=2
CHAT_TIMEOUT_SECONDS=120
```

```bash
sudo chown ansi:ansi /opt/ansi-assistant/current/.env
sudo chmod 600 /opt/ansi-assistant/current/.env
```

**`APP_ENV=production` n'est pas décoratif** : en développement, CORS accepte n'importe quel port de
`localhost` ; en production, il n'accepte que `FRONTEND_ORIGIN`. Se tromper ici ouvrirait l'API à
d'autres origines.

`CHAT_TIMEOUT_SECONDS` peut descendre à 120 sur GPU : la valeur de 300 existe parce qu'un portable
chargé dépasse facilement trois minutes.

---

## 6. Services

### 6.1 Ollama

`/etc/systemd/system/ollama.service` :

```ini
[Unit]
Description=Ollama
After=network-online.target

[Service]
ExecStart=/usr/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
# N'écoute que la boucle locale : le modèle n'est jamais exposé au réseau.
Environment="OLLAMA_HOST=127.0.0.1:11434"
# Garde le modèle chargé : le recharger coûte plusieurs secondes à chaque question.
Environment="OLLAMA_KEEP_ALIVE=24h"

[Install]
WantedBy=multi-user.target
```

### 6.2 L'API

`/etc/systemd/system/ansi-assistant.service` :

```ini
[Unit]
Description=Assistant documentaire ANSI
After=network-online.target postgresql.service ollama.service
Requires=postgresql.service

[Service]
Type=exec
User=ansi
Group=ansi
WorkingDirectory=/opt/ansi-assistant/current/backend
ExecStart=/opt/ansi-assistant/current/backend/.venv/bin/python -m uvicorn app.main:app \
          --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=5

# Cloisonnement
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/ansi-assistant/current/backend/data

[Install]
WantedBy=multi-user.target
```

> **`--workers 4` et la limitation de débit.** La limitation est aujourd'hui **en mémoire du
> processus** : avec quatre processus, un agent dispose en pratique de quatre fois le quota. Ce n'est
> pas un défaut de sécurité — l'authentification et le cloisonnement sont en base — mais si le quota
> doit être exact, il faut soit `--workers 1`, soit déporter le compteur (Redis). C'est la dette
> technique §7.9.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama ansi-assistant
```

### 6.3 nginx

`/etc/nginx/sites-available/ansi-assistant` :

```nginx
server {
    listen 443 ssl http2;
    server_name assistant.ansi.ne;

    ssl_certificate     /etc/ssl/ansi/assistant.crt;
    ssl_certificate_key /etc/ssl/ansi/assistant.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    # Aucune ressource distante : l'application n'en charge aucune, la politique le fige.
    add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; connect-src 'self'" always;

    client_max_body_size 25M;   # au-dessus de la limite applicative de 20 Mo

    root /opt/ansi-assistant/current/frontend/dist;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Le streaming des réponses doit arriver jeton par jeton.
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name assistant.ansi.ne;
    return 301 https://$host$request_uri;
}
```

> **`proxy_buffering off` n'est pas optionnel.** Sans cette ligne, nginx accumule la réponse et
> l'agent voit un écran figé pendant toute la génération, puis tout d'un coup. Le streaming serait
> perdu alors que le code fonctionne.

Le frontend est compilé avec l'URL de l'API :

```bash
VITE_API_URL=https://assistant.ansi.ne/api npm run build
```

---

## 7. Premier démarrage

```bash
# 1. Créer l'administrateur central (demande identifiant, service, mot de passe)
sudo -u ansi /opt/ansi-assistant/current/backend/.venv/bin/python -m app.create_admin

# 2. Vérifier que l'application répond
curl -s https://assistant.ansi.ne/api/health

# 3. Vérifier que rien ne sort de la machine, sur le serveur lui-même
cd /opt/ansi-assistant/current/backend
sudo -u ansi .venv/bin/python -m tests.isolation_probe
```

**Faites tourner `isolation_probe` sur le serveur, pas seulement en développement.** C'est la seule
preuve que la promesse d'isolement tient dans l'environnement réel, avec la configuration réelle.

Puis, dans l'ordre :

1. Créer les comptes, **avec un rôle et un service** pour chacun.
2. Importer d'abord les documents **transverses** — règlement intérieur, charte informatique.
3. Importer les documents de chaque service, en choisissant le bon service à l'import.
4. Vérifier avec un compte de chaque service qu'il voit le sien et pas celui des autres.

> **Suggestion pour une mise en service visible.** Le public visé comprend des stagiaires, et leurs
> questions sont prévisibles : congés, contacts, outils à installer, conventions de code. Un petit
> corpus d'accueil soigné, classé `transverse`, démontre la valeur immédiatement — et fait une
> bien meilleure démonstration qu'un versement en masse.

---

## 8. Sauvegardes

Trois choses à sauvegarder, et une seule est vraiment irremplaçable.

| Quoi | Où | Fréquence | Remarque |
|---|---|---|---|
| **Base PostgreSQL** | `pg_dump` | quotidienne | Comptes, documents, extraits, vecteurs, audit |
| **Fichiers importés** | `backend/data/documents/` | quotidienne | Les originaux |
| Modèles Ollama | `/usr/share/ollama` | une fois | Réinstallables depuis le colis |

```bash
#!/bin/bash
# /usr/local/bin/sauvegarde-ansi.sh
set -euo pipefail
JOUR=$(date +%F)
DEST=/var/sauvegardes/ansi/$JOUR
mkdir -p "$DEST"
sudo -u postgres pg_dump ansi_ai | gzip > "$DEST/base.sql.gz"
tar czf "$DEST/documents.tar.gz" -C /opt/ansi-assistant/current/backend/data documents
find /var/sauvegardes/ansi -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

**Une sauvegarde qui n'a jamais été restaurée n'est pas une sauvegarde.** Testez la restauration sur
une machine séparée avant la mise en service, et notez la date de ce test.

La base contient des **documents internes et l'historique des conversations** : elle est au moins
aussi sensible que le corpus lui-même et doit être chiffrée au repos.

---

## 9. Ce qui doit être arbitré avant les données réelles

Ces deux points ne sont pas techniques. Ils appartiennent à l'ANSI, et le code les attend.

### 9.1 La durée de rétention

`CONVERSATION_RETENTION_DAYS=0` conserve **indéfiniment**. Le mécanisme de purge existe et
fonctionne ; il lui manque une valeur. Trois questions :

- Combien de temps garder les conversations ? (30, 90, 365 jours ?)
- Le journal d'audit suit-il la même règle, ou une plus longue ?
- Qu'est-ce qui ne doit **jamais** être journalisé ?

### 9.2 Les dossiers individuels peuvent-ils être indexés ?

La consigne donnée au modèle pour les ressources humaines dit « réponds sur la règle, jamais sur une
personne ». **Cette consigne fonctionne, mais elle ne garantit rien.** La mesure l'a montré : à la
première rédaction, le modèle restituait le salaire d'un agent nommé ; il a fallu trouver la bonne
formulation pour que la règle tienne.

Une consigne n'est pas un contrôle d'accès. Deux options, toutes deux peu coûteuses :

1. **Ne pas indexer les dossiers individuels.** Le plus simple et le plus sûr.
2. **Marquer les documents porteurs de données nominatives** et les exclure de ce qui est transmis au
   modèle, tout en les laissant consultables directement par les rôles autorisés. Déterministe,
   appliqué avant le modèle, testable comme l'est le cloisonnement. Impose qu'un exploitant ne
   mélange pas dossiers individuels et règles dans un même document.

### 9.3 Un défaut connu à corriger avant la mise en service

**Changer un mot de passe ne révoque pas les sessions déjà ouvertes.** Une session compromise reste
valide jusqu'à 8 heures. Le correctif est court — un identifiant de session en base, ou un numéro de
version par compte inclus dans le jeton — et devrait être fait avant l'ouverture aux agents.

---

## 10. Mise à jour

```bash
# 1. Sauvegarder
/usr/local/bin/sauvegarde-ansi.sh

# 2. Déposer la nouvelle version à côté de l'ancienne
sudo -u ansi cp -r nouvelle-version /opt/ansi-assistant/2026-10-15
sudo -u ansi cp /opt/ansi-assistant/current/.env /opt/ansi-assistant/2026-10-15/.env

# 3. Dépendances, toujours hors ligne
sudo -u ansi /opt/ansi-assistant/2026-10-15/backend/.venv/bin/python -m pip install \
     --no-index --find-links=/chemin/wheels -r .../requirements.txt

# 4. Vérifier avant de basculer
cd /opt/ansi-assistant/2026-10-15/backend
sudo -u ansi .venv/bin/python -m pytest tests/ -q          # 230 contrôles, sans modèle
sudo -u ansi .venv/bin/python -m tests.isolation_probe     # rien ne sort
sudo -u ansi .venv/bin/python -m tests.security_probe      # injection de prompt

# 5. Basculer et redémarrer
sudo ln -sfn /opt/ansi-assistant/2026-10-15 /opt/ansi-assistant/current
sudo systemctl restart ansi-assistant
```

Le retour arrière consiste à refaire pointer le lien sur la version précédente et à redémarrer.
Gardez **au moins deux versions** en place.

Le schéma se met à jour tout seul : les colonnes manquantes sont ajoutées au démarrage. C'est
suffisant aujourd'hui et **à remplacer par Alembic** avant que le schéma ne devienne complexe — la
migration artisanale ajoute des colonnes, elle ne sait ni en renommer ni en supprimer.

---

## 11. Supervision

À surveiller, par ordre d'utilité :

| Quoi | Comment | Seuil |
|---|---|---|
| L'API répond | `GET /api/health` | toutes les minutes |
| Le modèle répond | `GET /api/system/status` | toutes les 5 minutes |
| Latence des réponses | journaux applicatifs | alerte au-delà de 30 s médianes |
| Espace disque | `df` | alerte à 80 % |
| Mémoire GPU | `nvidia-smi` | alerte à 90 % |
| Échecs de connexion | journal d'audit | pic inhabituel |

Les journaux applicatifs vont dans `journalctl -u ansi-assistant`. La sonde d'isolement vérifie qu'ils
ne contiennent ni contenu de document ni secret — **relancez-la après tout changement de niveau de
journalisation**, parce que passer en DEBUG est exactement ce qui pourrait y faire apparaître autre
chose.

---

## 12. Ce qui n'a jamais été mesuré

Par honnêteté, avant que quiconque bâtisse un plan dessus :

- **La charge.** Aucun test de montée en charge n'a été fait. Le chiffre de « 5 à 10 questions
  simultanées » de la section 2 est une estimation, pas une mesure.
- **Le temps jusqu'au premier jeton**, les jetons par seconde, la consommation RAM/VRAM réelle.
- **Le comportement d'un modèle 7B–8B sur GPU.** Tout ce qui est mesuré l'a été avec `qwen3:4b` sur
  processeur, sur un portable chargé.
- **La restauration d'une sauvegarde** dans cet environnement.

Ces quatre mesures sont le premier travail à faire une fois le serveur disponible. Elles sont rapides
et elles changent les réponses à donner à l'ANSI sur ce que l'assistant peut soutenir.

---

## Récapitulatif

```
[ Machine connectée ]                 [ Support ]          [ Serveur ANSI ]
  code + roues Python                     ──►                dpkg, venv
  frontend compilé                                           PostgreSQL + pgvector
  modèles Ollama                                             systemd × 2 + nginx
  paquets système                                            create_admin
  MANIFESTE.sha256                                           isolation_probe
                                                             import transverse → services
```

| Étape | Durée indicative |
|---|---|
| Préparer le colis | 1 à 2 h |
| Installer | 1 h |
| Configurer, TLS, services | 2 h |
| Vérifier, importer, contrôler le cloisonnement | 2 h |
| **Total** | **une journée** |

Hors les deux arbitrages de la section 9, qui ne dépendent pas de la technique — et qui, eux,
peuvent prendre bien plus longtemps.
