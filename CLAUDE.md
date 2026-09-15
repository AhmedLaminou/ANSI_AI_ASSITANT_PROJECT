# ANSI Local AI Assistant — contexte projet

## Ce qu'est ce projet

Assistant documentaire **hors ligne** pour l'ANSI (Agence Nationale pour la Sécurité des Systèmes
d'Information, Niger). Il répond aux questions des agents à partir des documents internes importés,
en citant ses sources, sans qu'aucune donnée ne quitte l'infrastructure.

Projet de stage, au stade **preuve de concept**. Ce n'est pas un produit déployé.

## Contrainte absolue : rien ne sort de la machine

C'est la raison d'être du projet. Toute proposition qui viole ce principe est à rejeter, même si
elle est techniquement plus simple :

- **Jamais** d'API IA externe (OpenAI, Anthropic, Gemini, OpenRouter…).
- **Jamais** de service cloud pour les embeddings, le RAG ou l'OCR.
- **Jamais** de CDN obligatoire au moment de l'exécution.
- L'inférence passe uniquement par Ollama en local (`127.0.0.1:11434`).

Vérifier aussi les **licences** avant d'ajouter une dépendance : PyMuPDF a été écarté au profit de
`pypdfium2` parce que l'AGPL poserait problème pour un déploiement ANSI.

## Commandes

Depuis `backend/` :

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --reload            # API
.\.venv\Scripts\python.exe -m pytest tests/test_units.py -q  # tests rapides, sans Ollama
.\.venv\Scripts\python.exe -m tests.smoke_rag                # bout en bout (lent, nécessite Ollama)
.\.venv\Scripts\python.exe -m tests.evaluate                 # qualité : exactitude, sources, refus, latence
.\.venv\Scripts\python.exe -m app.create_admin               # créer un administrateur
```

Depuis `frontend/` : `npm run dev` (développement) — **jamais sur un serveur**.

PostgreSQL + pgvector optionnel : `docker start ansi-pgvector`, puis renseigner `DATABASE_URL`.

## Pièges connus (constatés, pas supposés)

- **Le matériel est le facteur limitant.** Une question triviale demande ~21 s ; une réponse complète
  ~40 s, dont ~90 % de génération. Ce n'est pas un bug. Avant de conclure « le modèle est
  indisponible », vérifier la RAM libre et `CHAT_TIMEOUT_SECONDS`.
- **`qwen3:4b` émet son raisonnement dans `content`** malgré `think: false`, terminé par `</think>`.
  Il est retiré par `extract_answer()` et masqué pendant le streaming. Ne pas « simplifier » ce code.
- **Le seuil de similarité `0.18` ne sépare rien.** Mesuré : une question sans réponse obtient 0,354
  quand une question légitime obtient 0,344. C'est le **prompt système** qui refuse, pas le seuil.
  Ne pas prétendre le contraire dans la documentation.
- **Les tests partagent la base de développement.** Ils doivent porter sur leurs propres données,
  jamais supposer une base vide.
- **En développement, Vite bascule de port** (5173 → 5174…) ; le CORS accepte donc tout `localhost`
  hors production. En production, une seule origine est autorisée.
- **Les fichiers de langue Tesseract** vivent dans `backend/data/tessdata/` (non versionné) : c'est un
  artefact à transporter vers l'environnement hors ligne, au même titre que les poids des modèles.

## Ce qui ne doit jamais être versionné

`.env`, `credentials.txt`, `backend/data/` (base, documents importés, tessdata), `node_modules`,
`frontend/dist`, `.venv`. Vérifier avant chaque commit.

## Données de démonstration

N'utiliser que des documents **non sensibles ou anonymisés**. La politique de rétention n'est pas
encore arbitrée (`CONVERSATION_RETENTION_DAYS=0` conserve indéfiniment) : tant que ce point n'est pas
tranché, aucune donnée réelle ne doit être importée.

## Documentation du projet

- `README.md` — statut, démarrage, fonctionnalités
- `docs/A_PROPOS_DU_PROJET.md` — ce que fait le projet, différence avec un ChatGPT générique
- `docs/ARCHITECTURE_TECHNIQUE.md` — pipeline détaillé, mesures, dettes, feuille de route
- `architecture_agent_ia_offline_ANSI.md` — document de conception d'origine (la référence)

Quand une mesure est faite (latence, qualité, scores), **la consigner dans
`docs/ARCHITECTURE_TECHNIQUE.md`** avec sa date, plutôt que de la laisser dans une conversation.

## Manière de travailler attendue

- Exécuter librement les commandes de lecture, de test, de build et d'analyse — sans demander.
- **Demander avant toute action destructive ou irréversible** : suppression de fichiers ou de
  documents, `git push`, réinitialisation de base, purge de l'historique, arrêt de services que je
  n'ai pas démarrés, installation de logiciel système.
- Vérifier dans le navigateur toute modification de l'interface avant de la déclarer terminée.
- Préférer la mesure à l'affirmation : ce projet dispose d'un harnais d'évaluation, s'en servir.
- Signaler les résultats qui contredisent une hypothèse de conception plutôt que de les enjoliver.
