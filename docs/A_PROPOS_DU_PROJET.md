# À propos — que fait réellement ce projet ?

Ce document explique en langage clair ce que fait cet ensemble de fonctionnalités, ce qu'il n'est pas,
et en quoi il se distingue d'un assistant générique comme ChatGPT.

Pour les détails d'implémentation (pipeline RAG, modèle de données, LangGraph, pgvector, feuille de route),
voir [ARCHITECTURE_TECHNIQUE.md](ARCHITECTURE_TECHNIQUE.md).
Pour la vision d'ensemble et la théorie, voir [`architecture_agent_ia_offline_ANSI.md`](../architecture_agent_ia_offline_ANSI.md).

---

## 1. En une phrase

**Un assistant qui répond aux questions des agents de l'ANSI à partir des documents internes de l'ANSI,
en citant ses sources, sans qu'aucune donnée ne sorte de l'infrastructure, et en ne montrant à chaque
utilisateur que les documents auxquels son rôle donne accès.**

Ce n'est pas « un ChatGPT installé en local ». Le modèle de langage n'est qu'une pièce du système.

---

## 2. Ce qui se passe réellement quand on pose une question

```text
1. L'agent pose une question dans l'interface
        |
        v
2. Le backend vérifie son identité (cookie de session) et lit son rôle
        |
        v
3. La liste des documents est filtrée : on ne garde QUE ceux que ce rôle peut lire
        |
        v
4. La question est transformée en vecteur par embeddinggemma (local)
        |
        v
5. On compare ce vecteur aux extraits des documents autorisés (similarité cosinus)
        |
        v
6. Les 5 extraits les plus proches sont retenus
        |
        v
   Si aucun extrait n'est assez pertinent -> réponse : « information non trouvée »
        |                                     (pas d'invention)
        v
7. Les extraits + la question + les messages récents sont envoyés à qwen3:4b (local)
        |
        v
8. Le modèle rédige une réponse en français, en citant [S1], [S2]...
        |
        v
9. La réponse, ses sources et l'échange sont enregistrés dans la base locale
```

Si aucun extrait n'est assez proche, le système ne renonce pas immédiatement : il **reformule la
question** avec un vocabulaire plus proche de celui des documents et relance la recherche une fois.
« Combien de temps puis-je travailler depuis chez moi ? » ne ressemble pas à « le télétravail est
limité à 2 jours par semaine » — sans cette étape, la réponse existante resterait introuvable.

Point essentiel : **le modèle ne « connaît » pas les documents de l'ANSI.**
Il ne les a jamais appris. On lui donne les bons extraits au moment de la question,
et seulement ceux que l'utilisateur a le droit de voir. C'est le principe du RAG
(*Retrieval-Augmented Generation*).

---

## 3. Quelle différence avec ChatGPT ?

| | ChatGPT / assistant générique | Cet assistant ANSI |
|---|---|---|
| **Où vont les données** | La question part sur les serveurs du fournisseur, hors du pays | Rien ne quitte la machine : LLM, embeddings, index, documents sont locaux |
| **Connaissance des documents ANSI** | Aucune. Il faut coller le document à chaque fois | Les documents sont importés et indexés une fois, puis interrogeables |
| **Contrôle d'accès** | Aucun. Qui a le lien a la réponse | Chaque document porte une liste de rôles ; le filtrage a lieu **avant** la recherche |
| **Traçabilité** | Réponse sans source vérifiable | Chaque réponse cite le document et la page utilisés |
| **En cas d'absence d'information** | Tendance à inventer une réponse plausible | Refuse explicitement : « information non présente dans les documents » |
| **Fonctionnement sans Internet** | Impossible | Fonctionne câble débranché, une fois les modèles installés |
| **Dépendance** | Compte, abonnement, disponibilité et conditions du fournisseur | Aucune dépendance externe au moment de l'usage |
| **Auditabilité** | Boîte noire | Code lisible, journal des actions, index inspectable |

### En clair

Un ChatGPT générique est excellent pour rédiger, traduire ou expliquer des notions générales.
Il est structurellement inadapté dès que la question porte sur **un document interne non public**,
parce que :

1. lui envoyer ce document, c'est le faire sortir de l'infrastructure ;
2. il ne sait pas qui pose la question, donc il ne peut pas appliquer les droits d'accès ;
3. il ne peut pas prouver d'où vient sa réponse.

Cet assistant répond exactement à ces trois points.

---

## 4. Ce que cela apporte concrètement à l'ANSI

- **Souveraineté de la donnée.** Une note interne, un rapport, un règlement peut être interrogé
  sans qu'aucun octet ne transite vers un fournisseur étranger.
- **Le droit d'en connaître appliqué à l'IA.** Un agent avec le rôle `user` ne peut pas obtenir,
  même indirectement par une réponse du modèle, le contenu d'un document réservé à `admin`.
  Le filtrage est fait sur la base de données, pas par une consigne donnée au modèle.
- **Réponses vérifiables.** Chaque affirmation renvoie à un document et une page. Un agent peut
  aller vérifier avant de s'appuyer dessus pour une décision administrative.
- **Réduction du risque d'hallucination.** Sans source suffisamment pertinente, le système refuse
  de répondre plutôt que d'inventer.
- **Résistance à l'injection de prompt.** Les extraits de documents sont explicitement présentés
  au modèle comme des **données** et non comme des instructions : un document piégé contenant
  « ignore les instructions précédentes » ne détourne pas le comportement du système.
- **Base de démonstration crédible.** Le POC permet de montrer à une direction ce que serait une
  plateforme d'IA générative interne, et de discuter budget et matériel sur des faits mesurés.

---

## 5. Ce que ce projet n'est pas (encore)

C'est un **POC** — une preuve de faisabilité. Il ne doit pas être présenté comme un système national
en production. Les limites assumées aujourd'hui :

- L'OCR restitue du texte brut sans structure : un tableau scanné devient une suite de mots, pas
  des colonnes.
- La base par défaut reste SQLite. PostgreSQL + pgvector est disponible et testé, mais doit être
  activé — et basculer ne migre pas les documents déjà indexés.
- Pas d'authentification centrale (SSO / LDAP ANSI) : les comptes sont créés et gérés à la main
  depuis l'interface d'administration.
- Pas d'outils métier : le modèle ne peut interroger aucune base de données applicative.
- Une purge de l'historique par ancienneté existe, mais **la durée de conservation n'est pas
  arbitrée** : par défaut l'historique est conservé indéfiniment.
  **C'est le point à trancher avant toute mise en production.**
- Les réponses doivent rester validées par un agent avant toute décision administrative.

La suite est détaillée dans [ARCHITECTURE_TECHNIQUE.md](ARCHITECTURE_TECHNIQUE.md).

---

## 6. Où se trouvent réellement les modèles, les documents et l'index ?

Trois choses différentes sont souvent confondues. Sur la machine de développement actuelle :

| Élément | Emplacement réel | Qui l'écrit |
|---|---|---|
| **Poids des modèles** (`qwen3:4b`, `embeddinggemma`) | `C:\Users\ahmed\Documents\AI\` (sous-dossiers `blobs/` et `manifests/`) | Ollama, lors de `ollama pull` |
| **Documents importés** (fichiers d'origine) | `backend/data/documents/` | L'application, à l'import |
| **Index vectoriel + comptes + historique** | `backend/data/ansi_ai.db` (SQLite) | L'application |

### Pourquoi les modèles ne sont pas dans le dossier Ollama par défaut

Ollama stocke normalement ses modèles dans `%USERPROFILE%\.ollama\models`.
Ici, la variable d'environnement **`OLLAMA_MODELS`** pointe vers `C:\Users\ahmed\Documents\AI`,
donc c'est ce dossier qui contient réellement les poids.

**L'application ne lit jamais ces fichiers directement.** Elle ne connaît aucun chemin de modèle :
elle appelle l'API HTTP locale d'Ollama (`http://127.0.0.1:11434`) en demandant un modèle par son nom.
C'est Ollama qui résout le nom vers les fichiers, où qu'ils soient.

```text
Application  --HTTP-->  Ollama  --lit-->  OLLAMA_MODELS
(ne connaît               (résout          (C:\Users\ahmed\Documents\AI)
 que le nom               le nom)
 "qwen3:4b")
```

Conséquence pratique : **déplacer les modèles ne casse pas l'application**, tant que `OLLAMA_MODELS`
est correct et qu'`ollama list` affiche bien les deux modèles attendus.

L'écran « Vue d'ensemble » de l'interface affiche d'ailleurs si Ollama répond et si les deux modèles
sont bien présents — c'est le premier endroit à regarder en cas de problème.

---

## 7. Ce que le système conserve

| Donnée | Où | Sort de la machine ? |
|---|---|---|
| Fichiers importés | `backend/data/documents/` | Non |
| Extraits de texte + vecteurs | SQLite | Non |
| Questions et réponses | SQLite, liées au compte de l'utilisateur | Non |
| Comptes et mots de passe | SQLite, hachés avec Argon2 | Non |
| Journal des actions (import, suppression, question) | SQLite | Non |

L'historique d'un utilisateur n'est visible que par lui : chaque conversation est rattachée à son
compte, et un utilisateur ne peut ni lire ni supprimer celles d'un autre. Une conversation supprimée
l'est réellement, avec ses messages.

La purge par ancienneté se règle avec `CONVERSATION_RETENTION_DAYS` : elle s'exécute au démarrage de
l'API et peut être planifiée (`python -m app.purge_conversations`). Réglée à `0` — la valeur par
défaut — rien n'est supprimé : la durée doit être **décidée**, pas subie.

**Rappel de prudence :** tant que cette durée n'est pas arbitrée, n'utiliser que des documents non
sensibles ou anonymisés.
