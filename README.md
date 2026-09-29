# ANSI Local AI Assistant

Assistant documentaire interne de l'ANSI, **local et hors ligne**.

Il répond aux questions des agents **à partir des documents internes importés**, en citant ses
sources, sans qu'aucune donnée ne quitte la machine, et en ne montrant à chaque agent que les
documents que **son rôle et son service** autorisent.

> Ce n'est pas « un ChatGPT installé en local ». Le modèle de langage n'est qu'une pièce du système :
> l'essentiel est la recherche documentaire, le cloisonnement par service et la traçabilité autour de
> lui. Voir [docs/A_PROPOS_DU_PROJET.md](docs/A_PROPOS_DU_PROJET.md) pour la comparaison détaillée,
> et [explainer/FONCTIONNALITES.md](explainer/FONCTIONNALITES.md) pour l'inventaire complet.

## Statut

**Les fonctions métier sont achevées, mesurées et testées. Ce qui reste relève du déploiement, de la
gouvernance et de la montée en charge** — voir
[explainer/DEPLOYMENT_ON_ANSI_SERVERS.md](explainer/DEPLOYMENT_ON_ANSI_SERVERS.md).

| | État |
|---|---|
| Recherche documentaire, réponses sourcées, refus | ✅ Mesuré, 28/28 d'exactitude |
| Authentification par adresse, rôles, cycle de vie des comptes | ✅ Testé |
| Sessions révocables, mot de passe provisoire imposé, mot de passe oublié via l'administrateur | ✅ 20 contrôles |
| Cloisonnement par service (4 services + transverse) | ✅ Appliqué avant la recherche, 40 contrôles |
| Demande d'accès validée par l'administrateur | ✅ 16 contrôles |
| Consignes par service (l'assistant de chaque métier) | ✅ 32 contrôles, effet mesuré |
| Glossaires par service (« CP » ≠ « CP ») | ✅ 24 contrôles, effet mesuré |
| Outils répondant depuis la base, pas les documents | ✅ Cinq outils, droits appliqués |
| Supervision, journal d'audit et retours côté administrateur | ✅ Écran dédié, 31 contrôles |
| Profil de chaque agent, changement de mot de passe par lui-même | ✅ Écran dédié |
| Périmètre d'un document modifiable sans réimport | ✅ Administrateur, journalisé |
| Création de compte par adresse professionnelle, validée par l'administrateur | ✅ |
| Injection de prompt et fuite entre services | ✅ 17 contrôles |
| Fuite par les journaux, réseau sortant | ✅ 8 contrôles, sur le trafic réellement émis |
| Jeu d'évaluation **par service** | ✅ 34 questions, relevé par périmètre |
| Lacunes du corpus, contacts par service, réponses validées, questions de suite | ✅ 32 contrôles |
| Import groupé et import de dossier, responsables et dates de révision | ✅ 24 contrôles |
| Bibliothèque paginée : recherche sans accents, filtres avec compteurs, favoris, panneau de document | ✅ 25 contrôles |
| Interface : ce que chaque écran envoie, accessibilité (axe, contrastes mesurés) | ✅ 54 tests |
| Choix du modèle | ✅ Mesuré : la variante sans raisonnement égale l'actuelle, en 4,9 s au lieu de 34 s — **décision à prendre** |
| Politique de rétention et de journalisation | ❌ À arbitrer — **bloquant pour les données réelles** |
| Les dossiers individuels peuvent-ils être indexés ? | ❌ À arbitrer — une consigne ne protège pas |
| Conteneurisation, reverse proxy, supervision, sauvegardes | ❌ Procédure écrite, jamais exécutée |
| Tests de charge, mesures RAM/VRAM | ❌ Jamais faits |

**385 contrôles côté serveur et 54 côté interface**, hors ligne, plus six sondes nécessitant le modèle local.

Le détail est dans [docs/ARCHITECTURE_TECHNIQUE.md](docs/ARCHITECTURE_TECHNIQUE.md) §8. Tant que les
deux arbitrages ci-dessus ne sont pas rendus, n'utiliser que des documents non sensibles.

### Performances observées

Sur un poste de développement (CPU, 16 Go partagés avec l'IDE et le navigateur), avec `qwen3:4b` :
**≈ 34 s par réponse en médiane**. La recherche documentaire n'y est pour presque rien : lire la
consigne et les extraits prend au modèle 0,1 à 0,3 s, tout le reste est de l'écriture — et **97 % de
ce qu'il écrit est un raisonnement jeté** avant la réponse.

Aucun réglage ne le coupe : `qwen3:4b` est la variante *Thinking-2507*, qui raisonne toujours. Sa
jumelle sans raisonnement, `qwen3:4b-instruct-2507`, a été mesurée
([docs/ARCHITECTURE_TECHNIQUE.md](docs/ARCHITECTURE_TECHNIQUE.md) §5.8) : **même exactitude sur tout
le jeu (34/34), mêmes sondes de sécurité, 4,9 s de médiane au lieu de 34 s**. Pour l'essayer :

```ini
OLLAMA_CHAT_MODEL=qwen3:4b-instruct-2507-q4_K_M
OLLAMA_CHAT_REASONING=false
```

Un GPU reste nécessaire dès qu'il y a plusieurs agents simultanés, Ollama traitant les requêtes une
par une.

Ces chiffres décrivent **ce portable, pas la cible de déploiement** : deux exécutions identiques ont
donné 37,3 s puis 47,1 s de médiane, la charge de la machine dominant la mesure.


## Documentation

| Document | Contenu |
|---|---|
| [explainer/FONCTIONNALITES.md](explainer/FONCTIONNALITES.md) | **Inventaire complet de ce qui est implémenté**, où est le code, ce qui le vérifie |
| [explainer/DEPLOYMENT_ON_ANSI_SERVERS.md](explainer/DEPLOYMENT_ON_ANSI_SERVERS.md) | Mode opératoire de mise en production : colis hors ligne, PostgreSQL, systemd, nginx |
| [plan/IDEAS.md](plan/IDEAS.md) | **Ce que devient l'assistant branché sur les données vivantes** : annuaire, absences, agenda, actualités, événements |
| [docs/A_PROPOS_DU_PROJET.md](docs/A_PROPOS_DU_PROJET.md) | Ce que fait réellement le projet, différence avec ChatGPT, apport pour l'ANSI |
| [docs/ARCHITECTURE_TECHNIQUE.md](docs/ARCHITECTURE_TECHNIQUE.md) | Pipeline RAG détaillé, cloisonnement, consignes et glossaires par service, mesures, dettes techniques |
| [docs/FUTURE_IDEAS.md](docs/FUTURE_IDEAS.md) | Plan des phases A à G — en anglais, avec l'état de chacune |
| [docs/TEST_PLAN.md](docs/TEST_PLAN.md) | Procédure de test fonctionnel, test des droits, test hors ligne |
| [architecture_agent_ia_offline_ANSI.md](architecture_agent_ia_offline_ANSI.md) | Document de conception d'origine : théorie, risques, architecture cible |


## Fonctionnalités

### Recherche documentaire

- Import local de PDF, DOCX, TXT et Markdown (20 Mo maximum), découpage et indexation — un
  document, un **envoi groupé** de 50 fichiers, ou un **dossier entier** depuis le serveur
  (`python -m app.import_folder`), par une seule et même fonction d'ingestion.
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
- **Expansion des sigles, par service** : « la DSI » retrouve « direction des systèmes
  d'information ». Surtout, un même sigle ne veut pas dire la même chose partout : « CP » est un
  *congé payé* aux RH, un *crédit de paiement* aux finances, un *chef de projet* au technique.
  Chaque service lit la section commune plus la sienne ; un administrateur reçoit les deux
  lectures. Mesuré : à corpus et périmètre identiques, le premier résultat s'inverse selon le
  service. Le glossaire s'étend sans réindexer, dans `backend/data/glossary.json`.
- **Consignes par service** : les instructions données au modèle dépendent du service de l'agent.
  Un agent des finances reçoit « cite les montants avec leur exercice, n'additionne jamais deux
  chiffres que la source n'additionne pas » ; un agent RH reçoit « réponds sur la règle, jamais sur
  une personne ». Les garde-fous communs encadrent ces consignes et ne peuvent pas être affaiblis
  par elles — vérifié par 32 contrôles automatiques.
- **Date de validité** : une procédure expirée reste consultable mais est signalée comme périmée,
  au modèle comme à l'utilisateur.
- **Retour utilisateur** : un clic marque une réponse utile ou incorrecte ; chaque signalement
  alimente le jeu d'évaluation.
- **Lacunes du corpus** : chaque question restée sans réponse est enregistrée et regroupée par sens
  dans l'administration — « quatorze personnes ont demandé le télétravail partiel, et le corpus n'en
  dit rien ». Le seuil de regroupement a été mesuré, pas deviné.
- **Qui contacter** : tout refus indique le contact du service de l'agent ; « je ne trouve pas »
  devient « adressez-vous à… ».
- **Réponses validées** : une réponse relue par un administrateur est servie instantanément, sans
  passer par le modèle, signée de son nom et de sa date ; elle se publie en un clic depuis une bonne
  réponse de l'assistant. Même périmètre qu'un document.
- **Questions de suite** : « et pour un stagiaire ? » est cherchée avec la question qu'elle suit, et
  les documents déjà cités passent devant.
- **Responsables et dates de révision** : chaque document a un responsable et une échéance de
  relecture ; la supervision signale ce qui est en retard ou sans responsable.
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

- Authentification locale **par adresse professionnelle**, session par cookie `HttpOnly`, mots de
  passe hachés en Argon2.
- **Sessions révocables** : changer son mot de passe ferme ses autres sessions ; une
  réinitialisation ou une désactivation les ferme toutes ; un agent peut se déconnecter partout
  ailleurs, un administrateur fermer les sessions d'un poste perdu.
- **Mot de passe provisoire** : tout mot de passe choisi par un administrateur doit être remplacé à
  la connexion — un administrateur ne connaît jamais un mot de passe en usage.
- **Mot de passe oublié** : la demande part de l'écran de connexion vers l'administrateur, sans
  jamais toucher au mot de passe ni révéler si l'adresse existe.
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
- **Profil** de chaque agent : ses droits, ce qu'il peut lire et ne pas lire, son activité, les
  documents dont il est responsable, qui contacter.
- **Accessibilité** : contrastes mesurés (tous au-dessus de 4,5:1), navigation au clavier, lien
  d'évitement, champs et boutons nommés pour les lecteurs d'écran — vérifié par axe sur chaque
  écran principal.

## Démarrage local

1. Vérifier que les modèles sont présents : `ollama list` doit afficher `qwen3:4b` et `embeddinggemma`.
2. Créer le compte administrateur (adresse professionnelle, service, mot de passe) :
   `cd backend; .\.venv\Scripts\python.exe -m app.create_admin`.
3. Lancer l'API : `cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload`.
   (Toujours la forme `python -m` : sous Windows, Smart App Control bloque les lanceurs
   `.exe` générés par pip, qui ne sont pas signés.)
4. Dans une seconde fenêtre : `cd frontend; npm run dev`.
5. Ouvrir `http://localhost:5173` et se connecter.

Mot de passe oublié : l'agent dépose une demande depuis l'écran de connexion ; l'administrateur la
traite dans l'écran des comptes en fixant un mot de passe **provisoire**, que l'agent remplace à la
connexion suivante. Hors ligne, aucun lien ne peut partir par messagerie : la demande ne touche
jamais au mot de passe, sinon connaître une adresse suffirait à enfermer son titulaire dehors. Si
l'administrateur lui-même est bloqué, la remise à zéro se fait depuis la machine qui héberge la
base : `cd backend; .\.venv\Scripts\python.exe -m app.reset_password`.

### Tests et évaluation

Une seule fois : `cd backend; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`

Puis, depuis `backend`, avec Ollama démarré :

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_units.py -q   # logique pure, rapide, sans Ollama
.\.venv\Scripts\python.exe -m pytest tests/ -q                   # toute la suite hors ligne : 385 contrôles
.\.venv\Scripts\python.exe -m tests.smoke_rag                 # bout en bout, crée et supprime ses données
.\.venv\Scripts\python.exe -m tests.evaluate                  # qualité par service (exactitude, sources, refus, latence)
.\.venv\Scripts\python.exe -m tests.evaluate --model qwen3:0.6b   # comparer un autre modèle
.\.venv\Scripts\python.exe -m tests.evaluate --attempts 1         # mesurer l'apport de la reformulation
.\.venv\Scripts\python.exe -m tests.evaluate --department rh      # n'évaluer qu'un service (mesure rapide et ciblée)
.\.venv\Scripts\python.exe -m tests.security_probe              # injection de prompt et cloisonnement (nécessite Ollama)
.\.venv\Scripts\python.exe -m tests.registration_probe          # demande d'accès et approbation (sans Ollama)
.\.venv\Scripts\python.exe -m tests.prompt_probe                # les consignes par service changent-elles les réponses (Ollama)
.\.venv\Scripts\python.exe -m tests.glossary_probe              # le glossaire par service change-t-il le classement (Ollama, rapide)
.\.venv\Scripts\python.exe -m tests.isolation_probe             # rien ne sort de la machine, rien de sensible dans les journaux
```

Et depuis `frontend`, sans serveur ni modèle :

```powershell
npm test      # 54 tests d'interface : ce que chaque écran envoie, et axe sur chaque écran principal
```

Le jeu d'évaluation est **découpé par service**, et c'est le point : améliorer les réponses
techniques peut dégrader les réponses RH sans que rien ne le signale, parce qu'une moyenne
globale compense un service par un autre. Le relevé affiche donc une ligne par périmètre.

Il distingue aussi les questions **à formulation directe** (le vocabulaire de la question est
celui du document) des questions **à formulation éloignée** (« depuis chez moi » pour
« télétravail ») : ce sont ces dernières qui mesurent l'apport du graphe de décision.

Enfin, quatre questions sont **hors périmètre** : un agent pose une question légitime dont la
réponse appartient à un autre service. Ce n'est pas le cas malveillant — `tests.security_probe`
s'en charge — c'est le cas ordinaire, et ce qui se mesure est la tenue du refus.

`tests.evaluate` est le garde-fou à lancer après tout changement de modèle, de découpage ou de seuil.

Utiliser seulement des documents non sensibles ou anonymisés tant que les deux arbitrages du statut
ne sont pas rendus.

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
2. **Décider si les dossiers individuels peuvent être indexés** — une consigne au modèle n'est pas un
   contrôle d'accès.
3. **Durcir le jeu d'évaluation**, qui ne discrimine plus (34/34).
4. **Basculer sur `qwen3:4b-instruct-2507`** — mesuré : même exactitude, sondes réussies, 4,9 s au lieu
   de 34 s — puis confirmer sur le jeu durci et sur la machine cible.
5. Reprise SQLite → PostgreSQL, puis Alembic.
6. Mise en production — [explainer/DEPLOYMENT_ON_ANSI_SERVERS.md](explainer/DEPLOYMENT_ON_ANSI_SERVERS.md) —
   puis SSO/LDAP et le branchement sur les données vivantes de l'agence ([plan/IDEAS.md](plan/IDEAS.md)).
