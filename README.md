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
- Recherche, filtrage par classification et aperçu autorisé des documents indexés.

### Sécurité et contrôle d'accès

- Authentification locale, session par cookie `HttpOnly`, mots de passe hachés en Argon2.
- Rôles `admin`, `document_manager` et `user` ; chaque document porte la liste des rôles autorisés.
- Filtrage des documents **avant** la recherche sémantique, jamais par simple consigne au modèle.
- Extraits présentés au modèle comme des données non fiables (défense contre l'injection de prompt).
- Journal des actions : import, suppression, question répondue, création de compte.
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

Test de bout en bout (crée puis supprime ses propres données) :
`cd backend; .\.venv\Scripts\python.exe -m tests.smoke_rag`

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

1. Constituer un jeu d'évaluation (10 à 50 documents + questions/réponses attendues).
2. Ajouter les réponses en streaming.
3. Définir la politique de rétention et de journalisation — bloquant avant toute donnée réelle.
4. Migrer vers PostgreSQL + pgvector pour le passage à l'échelle.
5. Ajouter l'OCR local pour les PDF scannés.
6. Introduire LangGraph en même temps que le premier outil métier, à privilèges minimaux.
7. Intégrer le SSO/LDAP de l'ANSI, puis conteneurisation, supervision et sauvegardes.
