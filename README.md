# ANSI Local AI Assistant

POC local et hors ligne d'un assistant documentaire pour l'ANSI.

Il répond aux questions des agents **à partir des documents internes importés**, en citant ses sources,
sans qu'aucune donnée ne quitte la machine, et en ne montrant à chaque utilisateur que les documents
que son rôle autorise.

> Ce n'est pas « un ChatGPT installé en local ». Le modèle de langage n'est qu'une pièce du système :
> l'essentiel est la recherche documentaire, le contrôle d'accès et la traçabilité autour de lui.
> Voir [docs/A_PROPOS_DU_PROJET.md](docs/A_PROPOS_DU_PROJET.md) pour la comparaison détaillée avec un
> assistant générique.

## Statut : preuve de concept

**Ce dépôt n'est pas déployable en l'état sur l'infrastructure de l'ANSI.** Ce qui touche à l'IA et
au contrôle d'accès fonctionne et est mesuré ; ce qui manque relève du déploiement, de la gouvernance
et des tests de sécurité.

| | État |
|---|---|
| Recherche documentaire, réponses sourcées, refus | ✅ Fonctionne, mesuré |
| Authentification, rôles, cloisonnement par rôle | ✅ Fonctionne, testé |
| Conteneurisation, reverse proxy, supervision, sauvegardes | ❌ Rien |
| Politique de rétention et de journalisation | ❌ À arbitrer — **bloquant** |
| Tests d'injection de prompt et de cloisonnement | ✅ 17 contrôles passés |
| Tests de fuite par les logs et de réseau sortant | ❌ Jamais exécutés |
| Outils métier (répondre depuis la base, pas les documents) | ✅ Quatre outils, droits appliqués |
| Cloisonnement par service (4 services + transverse) | ✅ Appliqué avant la recherche, 40 tests |
| Demande d'accès validée par l'administrateur | ✅ 16 contrôles |
| Agents spécialisés : consignes et glossaires par service | ❌ Phases C et D — voir [docs/FUTURE_IDEAS.md](docs/FUTURE_IDEAS.md) |

Les points non traités sont détaillés dans
[docs/ARCHITECTURE_TECHNIQUE.md](docs/ARCHITECTURE_TECHNIQUE.md) §8. Tant qu'ils ne sont pas traités,
n'utiliser que des documents non sensibles ou anonymisés.

### Performances observées

Sur un poste de développement (CPU, 16 Go partagés avec l'IDE et le navigateur), avec `qwen3:4b` :
**≈ 21 s par réponse en médiane**. L'essentiel de ce temps n'est pas la recherche documentaire mais
le raisonnement interne du modèle, généré puis jeté. Un modèle sans phase de raisonnement est la
première piste d'accélération ; un GPU est nécessaire dès qu'il y a plusieurs utilisateurs
simultanés, Ollama traitant les requêtes une par une.

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
- **Routage d'intention** : une salutation (« salut », « merci ») reçoit une réponse immédiate qui
  rappelle le rôle de l'assistant et liste les documents interrogeables, au lieu de déclencher une
  recherche documentaire vouée à l'échec. Une politesse suivie d'une vraie question reste traitée
  comme une question.
- **Outils métier** : « combien d'utilisateurs sont enregistrés ? », « quels documents puis-je
  consulter ? » sont des requêtes en base, pas des recherches sémantiques. Réponse en **0,05 s** au
  lieu de 40 s. Le modèle ne décide jamais d'appeler un outil : l'appariement est déterministe et
  chaque outil applique les droits de l'appelant.
- **Recherche seule** : retrouve les passages sans rédiger de réponse, pour l'agent qui veut
  seulement identifier le bon document — quelques secondes au lieu d'une minute.
- **Expansion des sigles** : « la DSI » retrouve « direction des systèmes d'information ». Le
  glossaire s'étend sans réindexer, dans `backend/data/glossary.json`.
- **Consignes par service** : les instructions données au modèle dépendent du service de l'agent.
  Un agent des finances reçoit « cite les montants avec leur exercice, n'additionne jamais deux
  chiffres que la source n'additionne pas » ; un agent RH reçoit « réponds sur la règle, jamais sur
  une personne ». Les garde-fous communs encadrent ces consignes et ne peuvent pas être affaiblis
  par elles — vérifié par 32 contrôles automatiques.
- **Date de validité** : une procédure expirée reste consultable mais est signalée comme périmée,
  au modèle comme à l'utilisateur.
- **Retour utilisateur** : un clic marque une réponse utile ou incorrecte ; chaque signalement
  alimente le jeu d'évaluation.
- **Réponses en streaming** : le texte s'affiche au fil de la génération ; la phase de raisonnement
  du modèle est masquée et n'est jamais enregistrée.
- **Reformulation automatique** : quand la recherche ne ramène rien d'assez proche, la question est
  reformulée puis relancée une fois avant tout refus (graphe de décision LangGraph).
- **OCR local** des PDF scannés (Tesseract, français et anglais) : une page sans couche texte est
  rastérisée puis reconnue sur la machine, sans rien envoyer à l'extérieur.
- **Versionnement** : réimporter un fichier de même nom crée une version et remplace la précédente,
  qui n'est plus interrogée mais reste consultable.
- **PostgreSQL + pgvector** au choix (index HNSW), SQLite par défaut pour le POC.
- Recherche, filtrage par classification et aperçu autorisé des documents indexés.

### Sécurité et contrôle d'accès

- Authentification locale, session par cookie `HttpOnly`, mots de passe hachés en Argon2.
- Rôles `admin`, `document_manager` et `user` ; chaque document porte la liste des rôles autorisés.
- Filtrage des documents **avant** la recherche sémantique, jamais par simple consigne au modèle.
- **Cloisonnement par service** : quatre services (technique, finances, logistique, RH) ; un compte
  lit son service et les documents transverses, jamais le périmètre d'un autre. Le service
  **restreint et n'élargit jamais** : appartenir aux RH ne donne aucun droit sur un document RH dont
  les rôles autorisés vous excluent.
- **Demande d'accès** : un nouvel arrivant dépose une demande ; l'administrateur central accorde le
  rôle et le service, qui ne sont pas nécessairement ceux demandés. Un compte en attente ne lit rien
  et ne peut pas se connecter.
- Extraits présentés au modèle comme des données non fiables (défense contre l'injection de prompt).
- **Cycle de vie des comptes** : changement de rôle, désactivation/réactivation, réinitialisation de
  mot de passe — avec garde-fous contre le verrouillage du dernier administrateur.
- **Protection du formulaire de connexion** : les tentatives échouées sont comptées par compte *et*
  par adresse source, ce qui bloque aussi bien le forçage d'un compte que le balayage de plusieurs.
- **Limitation de débit** des questions, par compte (`CHAT_RATE_LIMIT_PER_MINUTE`).
- **Purge de l'historique** par ancienneté (`CONVERSATION_RETENTION_DAYS`, désactivée par défaut).
- Journal des actions : import, suppression, question répondue, gestion des comptes.
- Aucun accès direct du navigateur à Ollama ; aucune API IA externe.

### Espace de travail

- Écran d'accueil de l'assistant : **liste des documents réellement interrogeables** par le compte
  connecté, pour que le périmètre soit visible avant de poser la première question.
- Tableau de bord : disponibilité des services locaux, nombre de documents accessibles.
- Conversations personnelles : création, renommage, suppression, historique local par compte.
- Mémoire courte de conversation (6 derniers messages) sans partage entre comptes.
- Réponses formatées (listes, mise en gras, code) et copie en un clic.
- Thème clair / sombre, interface responsive, envoi au clavier (`Entrée`, `Maj+Entrée` pour un saut de ligne).

## Démarrage local

1. Vérifier que les modèles sont présents : `ollama list` doit afficher `qwen3:4b` et `embeddinggemma`.
2. Créer le compte administrateur : `cd backend; .\.venv\Scripts\python.exe -m app.create_admin`.
3. Lancer l'API : `cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload`.
   (Toujours la forme `python -m` : sous Windows, Smart App Control bloque les lanceurs
   `.exe` générés par pip, qui ne sont pas signés.)
4. Dans une seconde fenêtre : `cd frontend; npm run dev`.
5. Ouvrir `http://localhost:5173` et se connecter.

Mot de passe oublié : il n'existe volontairement aucune procédure de réinitialisation
par l'interface — l'assistant fonctionne hors ligne, il n'y a donc pas de relais de
messagerie pour envoyer un lien, et un tel point d'entrée non authentifié serait une
seconde porte. La remise à zéro se fait depuis la machine qui héberge la base :
`cd backend; .\.venv\Scripts\python.exe -m app.reset_password`.

### Tests et évaluation

Une seule fois : `cd backend; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`

Puis, depuis `backend`, avec Ollama démarré :

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_units.py -q   # logique pure, rapide, sans Ollama
.\.venv\Scripts\python.exe -m pytest tests/ -q                   # toute la suite hors ligne : 153 contrôles
.\.venv\Scripts\python.exe -m tests.smoke_rag                 # bout en bout, crée et supprime ses données
.\.venv\Scripts\python.exe -m tests.evaluate                  # qualité des réponses (exactitude, sources, refus, latence)
.\.venv\Scripts\python.exe -m tests.evaluate --model qwen3:0.6b   # comparer un autre modèle
.\.venv\Scripts\python.exe -m tests.evaluate --attempts 1         # mesurer l'apport de la reformulation
.\.venv\Scripts\python.exe -m tests.security_probe              # injection de prompt et cloisonnement (nécessite Ollama)
.\.venv\Scripts\python.exe -m tests.registration_probe          # demande d'accès et approbation (sans Ollama)
.\.venv\Scripts\python.exe -m tests.prompt_probe                # les consignes par service changent-elles les réponses (Ollama)
```

Le jeu d'évaluation distingue les questions **à formulation directe** (le vocabulaire de la question
est celui du document) des questions **à formulation éloignée** (« depuis chez moi » pour
« télétravail ») : ce sont ces dernières qui mesurent l'apport du graphe de décision.

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
