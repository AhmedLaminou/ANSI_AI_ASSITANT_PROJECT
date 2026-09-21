# De l'assistant documentaire à l'assistant interne de l'agence

*Ce que l'assistant pourrait faire une fois branché sur les données vivantes de l'ANSI.*
*Rédigé le 21 septembre 2026.*

Aujourd'hui l'assistant répond **à partir de documents importés**. C'est déjà utile, mais un agent
qui demande « qui contacter pour une carte d'accès ? » ou « suis-je en congé lundi ? » n'obtient rien,
parce que la réponse n'est dans aucun document : elle est dans un **système**.

Ce document explore ce que devient l'assistant lorsqu'on lui branche l'annuaire, les absences,
l'agenda, les actualités, les événements et les autres bases de l'agence. Il n'est pas une liste de
souhaits : chaque piste indique ce qu'elle apporte, **ce qu'elle ne doit jamais faire**, comment les
droits s'y appliquent, et ce qu'elle coûte.

Il prolonge la **phase E** de [`docs/FUTURE_IDEAS.md`](../docs/FUTURE_IDEAS.md), restée bloquée
faute d'API interne disponible.

---

## 1. La règle qui gouverne tout le reste

> **Un document s'indexe. Une donnée vivante se demande.**

C'est la décision structurante, et s'en écarter casserait l'assistant de façon silencieuse.

| | Document | Donnée vivante |
|---|---|---|
| Exemple | Procédure de congés | Solde de congés d'un agent |
| Nature | Stable, révisé de temps en temps | Change tous les jours |
| Accès | Découpé, vectorisé, retrouvé par similarité | **Requête au système source, au moment de la question** |
| Périmé ? | Tolérable, et signalé (date de validité) | **Inacceptable** — une donnée fausse est pire que pas de donnée |
| Autorité | La copie indexée | Le système source, jamais une copie |

**Pourquoi ne jamais indexer une donnée vivante.** Un index vectoriel ne se périme pas bruyamment :
il continue de répondre, avec assurance, à partir d'un instantané. « Amina est en congé » retrouvé
dans un extrait vieux de trois semaines est une réponse fausse, sourcée, et convaincante — le pire
des trois. Le système actuel refuse d'inventer ; il ne sait pas refuser d'être périmé.

L'architecture existante porte déjà les deux voies : les **outils** (§5.3) interrogent la base au
moment de la question, le **RAG** interroge le corpus. Brancher une nouvelle source, c'est écrire un
outil — pas un importeur.

---

## 2. Le motif de connecteur

Chaque source se branche de la même façon, et cette régularité est ce qui rend l'ensemble auditable.

```
Question ─► appariement déterministe ─► connecteur ─► API interne (lecture seule)
                                            │
                                            ├─ filtre les droits de l'appelant
                                            ├─ horodate la réponse
                                            └─ journalise l'appel
```

Six contraintes, reprises des outils existants et étendues :

1. **Le modèle ne choisit jamais d'appeler un connecteur.** Un motif déterministe le fait. Laisser
   un modèle décider quand interroger le SIRH est exactement ce que le §18 du document de conception
   déconseille — et une question forgée ne doit pas pouvoir déclencher une requête non prévue.
2. **Lecture seule, au début.** Aucun connecteur n'écrit dans un système de l'ANSI tant que la
   lecture n'est pas éprouvée. Une écriture ratée sur un système RH coûte bien plus qu'une réponse
   ratée.
3. **Aucun paramètre issu du texte de la question.** L'identité de l'appelant vient de sa session,
   jamais de ce qu'il a tapé. Sinon « les congés de Mariama » devient une façon de consulter le
   dossier de quelqu'un d'autre.
4. **Le connecteur applique les droits, pas l'interface.** Comme `can_access_document` :
   la décision est prise dans le code, avant que le modèle ne voie quoi que ce soit.
5. **Chaque réponse porte sa source et son horodatage.** Une réponse documentaire cite `[S1]` avec
   la page ; une réponse vivante doit dire *« selon le SIRH, le 21/09/2026 à 14 h 03 »*. Sans cela un
   agent ne peut pas savoir s'il regarde une règle ou un état.
6. **Panne = silence explicite.** Si le SIRH ne répond pas, l'assistant dit qu'il ne peut pas
   joindre le système — il ne se rabat pas sur un document qui parle du même sujet. Se rabattre
   produirait une réponse plausible et fausse.

**Identifiants et cloisonnement réseau.** Chaque connecteur a son propre compte de service, en
lecture seule, sur le seul système qu'il interroge. Un compte partagé « assistant » avec accès large
transformerait une faille dans l'assistant en accès général. Et le §1 de l'architecture — rien ne
sort de la machine — devient : *rien ne sort du réseau interne*. `tests/isolation_probe.py` devra
être étendu : la liste d'hôtes autorisés passe de `127.0.0.1` seul à une liste blanche explicite de
systèmes internes, et tout le reste reste interdit.

---

## 3. Les sources candidates

### 3.1 Annuaire des agents — *la plus utile, et de loin*

**Ce qu'elle rend possible :** « qui est le responsable de la logistique ? », « qui contacter pour
une carte d'accès ? », « quel est le poste de Sani Issakou ? », « qui remplace le responsable RH
pendant ses congés ? ».

C'est le premier besoin d'un nouvel arrivant, et l'assistant y est aveugle aujourd'hui. Pour un
public de stagiaires — l'audience déclarée du projet — c'est la fonctionnalité qui transforme
l'outil en réflexe quotidien.

**Ce qu'elle ne doit jamais faire :** restituer autre chose que des données d'annuaire. Un dossier
agent contient le poste et le bureau, mais aussi le salaire, l'ancienneté, les évaluations et les
sanctions. **Le connecteur ne doit exposer qu'une liste de champs explicitement autorisée**, décidée
à l'écriture du code, pas filtrée après coup.

C'est le point sur lequel la phase C a déjà buté : une consigne au modèle n'est pas un contrôle
d'accès. Ici la protection est structurelle — le champ `salaire` ne quitte jamais le connecteur.

**Coût :** faible, si un annuaire existe sous forme de base ou d'API. Un export CSV quotidien est un
repli acceptable, à condition d'afficher sa date.

---

### 3.2 Absences et congés

**Ce qu'elle rend possible :** « combien de jours de congé me reste-t-il ? », « qui est absent cette
semaine au service technique ? », « ma demande de congé a-t-elle été validée ? ».

**Deux périmètres radicalement différents, à ne jamais confondre :**

| Question | Qui peut l'obtenir |
|---|---|
| **Mon** solde, **mes** demandes | Moi seul |
| Qui est absent aujourd'hui dans **mon** service | Mon service — information d'organisation |
| Le solde de congés d'un **autre** agent | Personne par l'assistant. Le service RH. |

Le premier cas est naturel et attendu. Le troisième est exactement la donnée nominative que la
phase C a mesurée comme non protégeable par une consigne. La règle tient parce que le connecteur
**ne prend jamais de nom dans la question** : il interroge le SIRH avec l'identité de la session.

**Coût :** moyen. Dépend entièrement de l'existence d'une API SIRH. Le plus gros risque du projet en
matière de données personnelles — à n'ouvrir qu'après l'arbitrage du §6 de `FUTURE_IDEAS.md`.

---

### 3.3 Agenda, réunions et salles

**Ce qu'elle rend possible :** « quelles réunions ai-je demain ? », « la salle de réunion du 2ᵉ est-elle
libre jeudi à 10 h ? », « quand a lieu le prochain comité de direction ? ».

**Le piège :** le contenu d'un agenda est souvent plus sensible que son existence. « Réunion
disciplinaire — Amina S. » ne doit pas apparaître parce qu'un agent a demandé si une salle était
libre. Distinguer donc :

- **La disponibilité** (salle libre, créneau occupé) — largement partageable ;
- **Le titre et les participants** — à traiter comme une donnée personnelle.

**Une écriture, un jour, et une seule.** Réserver une salle est la première action qu'il serait
naturel de confier à l'assistant. C'est aussi le premier pas hors de la lecture seule : à ne faire
qu'avec confirmation explicite de l'agent, un journal dédié, et une annulation possible. À garder
pour plus tard.

**Coût :** moyen. Faible si l'agenda expose un calendrier standard en lecture.

---

### 3.4 Actualités et annonces internes

**Ce qu'elle rend possible :** « quelles sont les annonces de la semaine ? », « y a-t-il eu une note
de service sur le télétravail ? », « quoi de neuf au service technique ? ».

**Cas particulier intéressant : c'est la seule source qui peut légitimement être indexée.** Une
annonce est un texte daté et figé — un document, en somme. Elle relève donc du RAG, pas d'un
connecteur, à trois conditions : un import automatique, une date de publication, et une **date de
péremption** au-delà de laquelle l'annonce cesse d'être retournée. Le mécanisme de date de validité
existe déjà (§5.12).

Sans péremption, l'assistant ressortirait dans six mois une annonce de réunion passée — le défaut
classique des bases documentaires internes.

**Coût :** faible. C'est la piste la plus rapide à valeur visible.

---

### 3.5 Événements, formations, séminaires

**Ce qu'elle rend possible :** « quelles formations sont ouvertes ce trimestre ? », « comment
m'inscrire au séminaire cybersécurité ? », « le calendrier des congés collectifs ».

Proche des actualités, mais avec une dimension temporelle plus forte : **la question porte presque
toujours sur le futur**. Un assistant qui répond « la formation a eu lieu le 3 mars » à un agent qui
demande les formations disponibles n'a pas compris la question.

Impose que le connecteur comprenne une notion de **fenêtre temporelle** — à venir, en cours,
passé — et que la question soit routée avec cette notion. C'est une petite extension du graphe de
décision, pas une refonte.

**Coût :** faible à moyen.

---

### 3.6 Parc informatique et inventaire

**Ce qu'elle rend possible :** « quel est le matériel affecté à mon bureau ? », « combien de postes
sont en stock ? », « quand mon ordinateur a-t-il été remplacé ? ».

Naturellement rattaché aux services **technique** et **logistique**, et déjà prévu par la phase E.
Le cloisonnement existant s'y applique sans modification : un agent RH n'a pas à connaître le stock
de commutateurs.

**Attention à une fuite discrète :** un inventaire complet est une cartographie du système
d'information. « Combien de serveurs et de quelle marque » est une question d'exploitation légitime
et un renseignement utile à qui prépare une intrusion. Restreindre les **agrégats** au service
technique, même si les fiches unitaires sont plus largement lisibles.

**Coût :** moyen.

---

### 3.7 Tickets et demandes d'assistance

**Ce qu'elle rend possible :** « où en est ma demande de matériel ? », « combien de tickets ouverts
sur mon service ? », « quelle est la procédure pour signaler une panne ? ».

Complémentarité intéressante : la **procédure** est documentaire, l'**état d'une demande** est
vivant. Une bonne réponse combine les deux — et c'est précisément là que se pose la question du
§4.

**Coût :** moyen.

---

### 3.8 Marchés, achats, budget

**Ce qu'elle rend possible :** « quel est le plafond d'engagement sans validation ? » (déjà
documentaire), « où en est le marché de renouvellement des postes ? », « quel est le reste à
engager sur la ligne fournitures ? ».

**La source la plus sensible du lot**, et celle où le bloc de consignes « finances » de la phase C
prend tout son sens : ne jamais additionner, ne jamais arrondir, distinguer engagé et payé. Un
chiffre budgétaire faux dans une réponse d'assistant peut se retrouver dans une note.

À n'ouvrir qu'en dernier, avec un périmètre strictement finances, et des tests de non-calcul aussi
sévères que ceux de `prompt_probe.py`.

**Coût :** élevé.

---

## 4. Ce que ces sources cassent dans le modèle actuel

Ce ne sont pas des détails d'implémentation : ce sont les raisons pour lesquelles ce chantier
demande de la conception avant du code.

### 4.1 Les droits ne sont plus par document mais par champ

Le modèle actuel est simple et solide : **rôle × service**, appliqué à un document entier. Une fiche
agent ne se découpe pas ainsi. Le nom et le bureau sont de l'annuaire ; le salaire ne l'est pas.

Il faut donc introduire une notion que le code ne connaît pas : **la sensibilité d'un champ**, et une
liste blanche par connecteur. C'est le vrai travail de conception de cette phase, et le seul endroit
où `access.py` devra grandir.

La discipline existante s'applique : **une seule fonction décide**, jamais une règle recopiée dans
chaque connecteur.

### 4.2 Un document et une donnée vivante peuvent se contredire

La procédure dit « 30 jours de congés annuels ». Le SIRH dit « il vous reste 12 jours ». Les deux
sont vraies, à des niveaux différents, et l'assistant doit dire les deux sans les mélanger :
*« La règle générale est de 30 jours ouvrables [S1]. Selon le SIRH, au 21/09/2026, votre solde est
de 12 jours. »*

Le risque réel est la synthèse abusive — « il vous reste 30 jours » — qui est une réponse fausse
construite à partir de deux réponses justes. **Ce cas mérite son propre jeu de tests**, sur le modèle
de `prompt_probe.py` : une valeur qui n'apparaît que si la fusion a eu lieu.

### 4.3 La fraîcheur devient une propriété visible

Un agent doit pouvoir distinguer « la règle » de « l'état au moment où je pose la question ». Cela
impose, dans l'interface, deux formes de source distinctes : la citation documentaire `[S1]` qu'on
connaît, et un bandeau de fraîcheur pour les données vivantes.

### 4.4 Une panne partielle n'est plus une panne

Aujourd'hui, si Ollama tombe, tout tombe et c'est clair. Demain, le SIRH peut être indisponible
pendant que le reste fonctionne. L'assistant doit répondre **partiellement et le dire** — jamais
compléter un trou par une supposition.

### 4.5 La rétention se complique

L'arbitrage en attente porte sur l'historique des conversations. Si une conversation contient
désormais le solde de congés d'un agent, **l'historique devient lui-même un fichier de données
personnelles**. Cela renforce l'argument du §6.4 de `FUTURE_IDEAS.md` : la durée de rétention doit
être fixée avant, pas après.

---

## 5. Ce qu'il ne faut pas construire

Aussi utile que la liste précédente.

- **Pas de synchronisation.** Ne pas copier la base RH de l'ANSI dans celle de l'assistant. Une copie
  diverge, double la surface d'attaque et double le problème de rétention. Le système source reste
  l'autorité.
- **Pas de SQL libre, jamais.** Même « l'administrateur pourrait poser des questions libres à la
  base ». C'est la porte que le §18 ferme, et la rouvrir pour un seul rôle suffit à l'ouvrir.
- **Pas d'écriture avant que la lecture soit éprouvée**, et jamais sans confirmation explicite.
- **Pas de connecteur sans test de fuite.** Chaque source nouvelle est une nouvelle façon pour
  l'assistant de divulguer. La méthode des canaris de `security_probe.py` s'étend : planter une
  valeur unique dans un champ interdit et vérifier qu'elle ne ressort par aucun chemin.
- **Pas de données individuelles dans ce qui est transmis au modèle**, tant que l'arbitrage du §6
  n'est pas rendu. Un connecteur peut légitimement renvoyer « vous avez 12 jours » à son
  propriétaire ; il ne doit pas verser une fiche agent dans le contexte de génération.

---

## 6. Ordre de travail proposé

L'ordre suit la valeur par unité de risque, pas la facilité.

| | Source | Valeur | Risque | Dépend de |
|---|---|---|---|---|
| 1 | **Actualités et annonces** | élevée | faible | un flux ou un dossier partagé |
| 2 | **Annuaire des agents** | très élevée | faible si liste blanche stricte | un annuaire interrogeable |
| 3 | **Événements et formations** | moyenne | faible | un calendrier |
| 4 | **Parc informatique** | moyenne | moyen | un inventaire |
| 5 | **Tickets** | moyenne | moyen | un outil de ticketing |
| 6 | **Absences et congés** | élevée | **élevé** | API SIRH **et** arbitrage §6 |
| 7 | **Agenda et salles** | moyenne | moyen à élevé | un calendrier partagé |
| 8 | **Budget et marchés** | élevée pour les finances | **élevé** | à ouvrir en dernier |

Les deux premières lignes sont réalisables sans rien changer au modèle de droits. C'est là qu'il faut
commencer : elles démontrent la valeur, et elles laissent le temps de concevoir proprement la
sensibilité par champ avant d'en avoir besoin.

---

## 7. Ce qu'il faut demander à l'ANSI

Le code ne peut pas avancer sur ce chantier sans réponses. Autant les poser tôt et ensemble.

1. **Quels systèmes existent réellement**, et lesquels exposent une API ou une base interrogeable ?
   Un export nocturne est un repli acceptable ; une saisie manuelle ne l'est pas.
2. **Qui a le droit de savoir quoi**, service par service — en particulier : l'absence d'un collègue
   est-elle une information d'organisation ou une donnée personnelle ?
3. **Un compte de service en lecture seule** par système, et l'accord de son responsable.
4. **La durée de rétention** — déjà bloquante, et davantage encore dès qu'une conversation peut
   contenir une donnée personnelle.
5. **Qui corrige une donnée fausse ?** Si l'assistant restitue fidèlement une erreur du SIRH, le
   défaut est dans le SIRH. Le circuit de signalement doit exister avant l'ouverture aux agents.

---

## 8. Ce que devient l'assistant

Pour rendre le résultat concret, voici ce qu'un agent pourrait poser, par service, une fois les
sources 1 et 2 branchées :

**Tout agent** — « qui contacter pour une carte d'accès ? » · « quelles annonces cette semaine ? » ·
« où en est ma demande ? » · « quelles formations sont ouvertes ? »

**Technique** — « qui est d'astreinte cette semaine ? » · « quelle est la fenêtre de maintenance ? » ·
« combien de postes en stock ? »

**Finances** — « quel est le plafond d'engagement ? » · « où en est le marché des postes ? »

**Logistique** — « quel est le délai de livraison contractuel ? » · « quel matériel est affecté au
bureau 204 ? »

**RH** — « quelle est la procédure de congés ? » · « combien de jours me reste-t-il ? » *(à moi
seul)* · « qui est absent au service technique cette semaine ? »

**Administrateur** — tout ce qui précède, plus la supervision existante : corpus par service,
journal d'audit, retours.

C'est là que l'outil cesse d'être une recherche documentaire améliorée pour devenir ce que son nom
annonce : **l'assistant interne de l'agence**.

---

## 9. Ce que ce document n'affirme pas

Par honnêteté, avant que quiconque en fasse un plan de charge :

- **Aucune de ces sources n'a été vue.** Les noms de systèmes cités — SIRH, inventaire, ticketing —
  sont des catégories, pas des produits identifiés à l'ANSI.
- **Les coûts indiqués sont relatifs**, pas des estimations en jours : ils classent les pistes entre
  elles, rien de plus.
- **Rien n'est commencé.** Le seul travail déjà fait qui s'y rattache est le motif des outils
  (§5.3 de l'architecture), qui a été conçu pour être étendu exactement de cette façon.
- **La sensibilité par champ n'est pas conçue**, seulement identifiée comme le vrai travail. C'est
  l'étape à ne pas improviser.
