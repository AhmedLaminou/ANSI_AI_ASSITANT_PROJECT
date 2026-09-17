# Pistes d'évolution — recommandations du tuteur de stage

Deux recommandations ont été formulées lors du point de stage :

1. **Des agents spécialisés par département** (RH, technique, finances…).
2. **L'assistant doit fonctionner sans Internet.**

Ce document examine les deux : ce qui existe déjà, ce que cela demanderait réellement, et les
pistes qui en découlent.

---

## 1. Fonctionner sans Internet — déjà acquis

C'est la raison d'être du projet depuis le départ, et c'est vérifiable :

| Composant | Où il s'exécute |
|---|---|
| Modèle de génération (`qwen3:4b`) | Ollama local, `127.0.0.1:11434` |
| Modèle d'embeddings (`embeddinggemma`) | Ollama local |
| Index vectoriel | SQLite ou PostgreSQL local |
| OCR (Tesseract) | binaire local, sous-processus |
| Documents et historique | disque local |

Aucune API d'IA externe, aucun service cloud pour les embeddings ou le RAG, aucun CDN requis à
l'exécution. Le navigateur ne parle jamais directement à Ollama : tout passe par le backend, seul
endroit où les droits sont évalués.

**Ce qui reste à faire pour que ce soit vrai *en production*, pas seulement en développement :**

- **Couper réellement le réseau sortant.** Aujourd'hui le poste de développement a Internet ; rien
  ne le prouve inutile. La démonstration attendue est celle du [TEST_PLAN.md](TEST_PLAN.md) :
  débrancher Wi-Fi et Ethernet, puis poser une question et obtenir une réponse sourcée.
- **Politique `deny by default` en sortie** sur le serveur de production (§17 du document de
  conception).
- **Procédure de mise à jour hors ligne** (§25) : télécharger et vérifier modèles et paquets dans un
  environnement connecté, contrôler empreintes et licences, puis transférer. Le critère qui distingue
  un système réellement hors ligne d'un système « qui marche sans Internet, sauf au démarrage » :
  aucune étape d'installation ne doit exiger un accès réseau sortant.
- **Transporter les artefacts non versionnés** : poids des modèles, fichiers de langue Tesseract
  (`backend/data/tessdata/`), paquets Python, frontend compilé.

Détail complet dans [ARCHITECTURE_TECHNIQUE.md §5.7](ARCHITECTURE_TECHNIQUE.md).

---

## 2. Des agents spécialisés par département

### Ce que « agent spécialisé » ne devrait pas vouloir dire

Le mot « agent » laisse souvent entendre *un modèle par département*. Ce serait le plus coûteux et
le moins utile des choix : quatre départements signifieraient quatre modèles à charger, sur une
machine où un seul modèle consomme déjà l'essentiel de la mémoire et où une réponse demande ~40 s
(§5.11). La spécialisation utile ne vient pas du modèle — elle vient **du corpus, des droits et des
consignes**.

### Ce que la spécialisation apporte réellement

| Levier | Effet | Coût |
|---|---|---|
| **Cloisonner le corpus** | un agent RH ne voit que les documents RH | faible, la structure existe |
| **Consigne système par département** | vocabulaire et ton adaptés, ce qu'il faut refuser | très faible |
| **Glossaire par département** | les sigles RH ne sont pas les sigles techniques | très faible, mécanisme déjà là |
| **Outils par département** | « combien de congés restants ? » interroge le SIRH | moyen, dépend des API internes |
| **Jeu d'évaluation par département** | savoir si les réponses RH se dégradent | moyen |
| Un modèle par département | quasi nul ici | très élevé |

### Ce qui existe déjà et sert de fondation

- **Le filtrage avant recherche.** Chaque document porte une liste de rôles ; un extrait non autorisé
  n'est jamais candidat. Le mécanisme de cloisonnement est en place — il manque le bon axe.
- **Le graphe de décision.** Un routeur existe déjà et dispatche entre social, outil et documentaire
  (§5.1). Ajouter un axe départemental s'y insère sans rien remettre en cause.
- **Les outils métier** (§5.3), déterministes, avec rôles déclarés et droits de l'appelant appliqués.
- **Le glossaire** (§5.3 ter), extensible sans réindexer.

### Le vrai travail : séparer deux axes qui sont aujourd'hui confondus

C'est le point de conception central, et il est facile à manquer.

- **Le rôle** dit *ce qu'on a le droit de faire* : lire, gérer les documents, administrer.
- **Le département** dit *de quel périmètre on relève* : RH, technique, finances.

Ce sont deux axes indépendants. Un gestionnaire documentaire RH et un gestionnaire documentaire
technique ont le même rôle et des périmètres différents. Un directeur peut avoir besoin d'un accès
transverse. Aujourd'hui le projet n'a qu'un seul axe — le rôle — et il porte les deux sens à la fois.

Modèle de données proposé :

```text
users        : role (admin | document_manager | user)
             + departments[]        (un agent peut relever de plusieurs)

documents    : allowed_roles        (existant, inchangé)
             + department           (RH | technique | finances | transverse)

accès = (le rôle de l'agent est autorisé sur le document)
        ET (le département du document ∈ départements de l'agent  OU  document transverse)
```

Le `ET` est important : le département **restreint** l'accès, il ne l'élargit jamais. Un agent RH ne
gagne aucun droit sur un document RH qui ne l'autorise pas par son rôle.

### Comment savoir de quel département relève une question ?

Trois approches, par ordre de préférence :

1. **Sélecteur explicite dans l'interface** (recommandé). L'agent choisit « RH », « technique » ou
   « tous mes départements ». Déterministe, instantané, auditable, aucun coût de latence. Cohérent
   avec le choix déjà fait pour les outils : le modèle ne décide pas ce qui touche aux droits.
2. **Tout chercher dans le périmètre autorisé** (comportement actuel), en laissant la pertinence
   trancher. Simple, mais devient bruyant quand le corpus grandit.
3. **Classification automatique par le modèle.** Séduisant, mais ajoute un appel au modèle — donc des
   dizaines de secondes — et introduit une décision non déterministe sur un axe qui touche au
   périmètre documentaire. À éviter tant que le point 1 suffit.

### Mise en œuvre progressive

**Étape 1 — le périmètre.** Ajouter `department` aux documents et `departments[]` aux comptes,
étendre le filtrage ACL, ajouter le sélecteur dans l'interface. C'est l'essentiel de la valeur et
cela ne touche pas au modèle. Aucun réindexage nécessaire.

**Étape 2 — la voix.** Une consigne système par département, en plus de la consigne commune : le
vocabulaire RH n'est pas le vocabulaire technique, et ce qu'il faut refuser diffère (un agent RH ne
doit pas se voir répondre sur la configuration d'un pare-feu).

**Étape 3 — le vocabulaire.** Un glossaire par département. Le mécanisme existe ; il suffit de le
découper. Attention au piège déjà rencontré : un sigle de deux lettres entre en collision avec un mot
courant (« SI » contre « si »), et le même sigle peut vouloir dire deux choses selon le département —
raison de plus pour que les glossaires soient séparés.

**Étape 4 — les outils.** C'est là que les départements prennent tout leur sens : « combien de jours
de congés me reste-t-il ? » relève du SIRH, « quel est l'état du parc ? » de l'inventaire technique.
Chaque outil doit rester ce qu'ils sont aujourd'hui : fonction fixe, aucun paramètre issu de la
question, droits de l'appelant appliqués, invocation journalisée (§18).

**Étape 5 — la mesure.** Un jeu d'évaluation par département. Sans cela, une amélioration côté
technique peut dégrader les réponses RH sans que personne ne s'en aperçoive.

---

## 3. Pistes complémentaires, non demandées mais cohérentes

Elles découlent du découpage par département et méritent d'être discutées au même moment.

**Un référent par document.** Qui contacter quand une procédure est périmée ou ambiguë ? Associé aux
dates de validité (§5.12), cela transforme un refus en action : « cette procédure a expiré le
30/06/2026 — référent : direction des ressources humaines ».

**Une réponse « qui interroger ».** Quand rien n'est trouvé dans le périmètre de l'agent, indiquer
le département qui détient probablement l'information plutôt que de s'arrêter à un refus. Utile
précisément parce que le cloisonnement empêche de voir au-delà de son périmètre.

**Import en lot.** Importer 500 documents un par un via l'interface n'est pas réaliste. Un import
par dossier, avec département et rôles déduits de l'arborescence, est nécessaire avant tout
déploiement réel.

**Statistiques par département.** Les outils existants comptent sur le périmètre de l'appelant ;
un administrateur a besoin de la vue par département pour savoir quel fonds est couvert et lequel ne
l'est pas.

**Tableau de bord des retours.** Le mécanisme de retour utilisateur (§5.13) collecte déjà les
signalements ; les regrouper par département indiquerait où la qualité décroche en premier.

---

## 4. Ce qu'il faut trancher avant de commencer

1. **La liste des départements** et leur granularité. Trop fine, elle multiplie les cloisons et les
   questions sans réponse ; trop grossière, elle ne sépare rien.
2. **Le statut des documents transverses** (règlement intérieur, charte informatique) : visibles de
   tous, ou rattachés à un département propriétaire avec lecture élargie ?
3. **Les accès transverses** : qui peut interroger plusieurs départements, et selon quelle règle ?
4. **La politique de rétention**, toujours non arbitrée : elle reste le point bloquant avant toute
   donnée réelle, quel que soit le découpage retenu.

Les points 1 à 3 sont des décisions d'organisation, pas des choix techniques : ils appartiennent à
l'ANSI, et le code doit s'y conformer — pas l'inverse.
