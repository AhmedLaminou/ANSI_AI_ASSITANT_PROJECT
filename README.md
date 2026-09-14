# ANSI Local AI Assistant

POC local et hors ligne d'un assistant documentaire pour l'ANSI.

Il répond aux questions des agents **à partir des documents internes importés**, en citant ses sources,
sans qu'aucune donnée ne quitte la machine, et en ne montrant à chaque utilisateur que les documents
que son rôle autorise.

> Ce n'est pas « un ChatGPT installé en local ». Le modèle de langage n'est qu'une pièce du système :
> l'essentiel est la recherche documentaire, le contrôle d'accès et la traçabilité autour de lui.
> Voir [docs/A_PROPOS_DU_PROJET.md](docs/A_PROPOS_DU_PROJET.md) pour la comparaison détaillée avec un
> assistant générique.

## Documentation

| Document | Contenu |
|---|---|
| [docs/A_PROPOS_DU_PROJET.md](docs/A_PROPOS_DU_PROJET.md) | Ce que fait réellement le projet, différence avec ChatGPT, apport pour l'ANSI, emplacement des modèles et des données |
| [docs/ARCHITECTURE_TECHNIQUE.md](docs/ARCHITECTURE_TECHNIQUE.md) | Pipeline RAG détaillé, modèle de données, sécurité, et ce qui reste à faire (LangGraph, pgvector, OCR, SSO, déploiement) |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Procédure de test fonctionnel, test des droits, test hors ligne |
| [architecture_agent_ia_offline_ANSI.md](architecture_agent_ia_offline_ANSI.md) | Document de conception d'origine : théorie, risques, architecture cible |

## Fonctionnalités du POC

### Recherche documentaire

- Import local de PDF, DOCX, TXT et Markdown (20 Mo maximum), découpage et indexation.
- Embeddings locaux avec `embeddinggemma` (768 dimensions), recherche par similarité cosinus.
- Réponses générées par `qwen3:4b` avec citation du document et de la page (`[S1]`, `[S2]`…).
- Refus explicite lorsqu'aucune source pertinente n'est trouvée, au lieu d'une réponse inventée.
- **Réponses en streaming** : le texte s'affiche au fil de la génération ; la phase de raisonnement
  du modèle est masquée et n'est jamais enregistrée.
- **Versionnement** : réimporter un fichier de même nom crée une version et remplace la précédente,
  qui n'est plus interrogée mais reste consultable.
- Recherche, filtrage par classification et aperçu autorisé des documents indexés.

### Sécurité et contrôle d'accès

- Authentification locale, session par cookie `HttpOnly`, mots de passe hachés en Argon2.
- Rôles `admin`, `document_manager` et `user` ; chaque document porte la liste des rôles autorisés.
- Filtrage des documents **avant** la recherche sémantique, jamais par simple consigne au modèle.
- Extraits présentés au modèle comme des données non fiables (défense contre l'injection de prompt).
- **Cycle de vie des comptes** : changement de rôle, désactivation/réactivation, réinitialisation de
  mot de passe — avec garde-fous contre le verrouillage du dernier administrateur.
- **Limitation de débit** par compte (`CHAT_RATE_LIMIT_PER_MINUTE`).
- **Purge de l'historique** par ancienneté (`CONVERSATION_RETENTION_DAYS`, désactivée par défaut).
- Journal des actions : import, suppression, question répondue, gestion des comptes.
- Aucun accès direct du navigateur à Ollama ; aucune API IA externe.

### Espace de travail

- Tableau de bord : disponibilité des services locaux, nombre de documents accessibles.
- Conversations personnelles : création, renommage, suppression, historique local par compte.
- Mémoire courte de conversation (6 derniers messages) sans partage entre comptes.
- Réponses formatées (listes, mise en gras, code) et copie en un clic.
- Thème clair / sombre, interface responsive, envoi au clavier (`Entrée`, `Maj+Entrée` pour un saut de ligne).

## Démarrage local

1. Vérifier que les modèles sont présents : `ollama list` doit afficher `qwen3:4b` et `embeddinggemma`.
2. Créer le compte administrateur : `cd backend; .\.venv\Scripts\python.exe -m app.create_admin`.
3. Lancer l'API : `cd backend; .\.venv\Scripts\uvicorn.exe app.main:app --reload`.
4. Dans une seconde fenêtre : `cd frontend; npm run dev`.
5. Ouvrir `http://localhost:5173` et se connecter.

### Tests et évaluation

Une seule fois : `cd backend; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`

Puis, depuis `backend`, avec Ollama démarré :

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_units.py -q   # logique pure, rapide, sans Ollama
.\.venv\Scripts\python.exe -m tests.smoke_rag                 # bout en bout, crée et supprime ses données
.\.venv\Scripts\python.exe -m tests.evaluate                  # qualité des réponses (exactitude, sources, refus, latence)
.\.venv\Scripts\python.exe -m tests.evaluate --model qwen3:0.6b   # comparer un autre modèle
```

`tests.evaluate` est le garde-fou à lancer après tout changement de modèle, de découpage ou de seuil.

Utiliser seulement des documents non sensibles ou anonymisés. Les PDF scannés ne sont pas encore pris
en charge : une étape OCR locale sera ajoutée séparément.

## Où se trouvent les modèles et les données

| Élément | Emplacement |
|---|---|
| Poids des modèles Ollama | dossier pointé par la variable `OLLAMA_MODELS` (sinon `%USERPROFILE%\.ollama\models`) |
| Documents importés | `backend/data/documents/` |
| Index vectoriel, comptes, historique | `backend/data/ansi_ai.db` |

L'application ne lit aucun fichier de modèle directement : elle demande un modèle **par son nom** à
l'API locale d'Ollama (`http://127.0.0.1:11434`), et c'est Ollama qui résout le nom vers les fichiers.
Déplacer les modèles ne casse donc pas l'application.

## Étapes suivantes

Détail et justification dans [docs/ARCHITECTURE_TECHNIQUE.md](docs/ARCHITECTURE_TECHNIQUE.md) §8.

1. **Arbitrer la politique de rétention et de journalisation** — le mécanisme existe, la durée reste
   à décider. Bloquant avant toute donnée réelle.
2. Comparer plusieurs modèles avec `tests.evaluate`, en particulier un modèle sans raisonnement :
   les jetons de raisonnement dominent aujourd'hui la latence.
3. Ajouter l'OCR local pour les PDF scannés (nécessite Tesseract).
4. Migrer vers PostgreSQL + pgvector (nécessite l'extension `pgvector`).
5. Introduire LangGraph en même temps que le premier outil métier, à privilèges minimaux.
6. Intégrer le SSO/LDAP de l'ANSI, puis conteneurisation, supervision et sauvegardes.
