# Machine de déploiement — cahier des charges

*Demande de mise à disposition d'un serveur pour l'assistant documentaire local de l'ANSI.*
*Rédigé le 1er octobre 2026. Destinataire : responsable technique / équipe infrastructure.*

Ce document dit **quelle machine préparer** et **ce dont le projet a besoin de l'équipe
infrastructure**. La procédure d'installation elle-même est dans
[`DEPLOYMENT_ON_ANSI_SERVERS.md`](DEPLOYMENT_ON_ANSI_SERVERS.md).

---

## 1. En résumé

| | Demande |
|---|---|
| **Type** | **Une machine virtuelle** (ou un serveur physique) sous **Ubuntu Server 24.04 LTS** |
| **GPU** | **1 × NVIDIA 48 Go** (L40S recommandé) attribué **en passthrough** à la VM — minimum acceptable : 24 Go (L4, A10) |
| **vCPU** | **16** |
| **RAM** | **64 Go** |
| **Disque** | **500 Go SSD NVMe** (système + application) + **un volume de sauvegarde séparé** |
| **Réseau** | Réseau interne uniquement. Entrée : 443 (et 80 redirigé). **Aucune sortie vers Internet.** |
| **Nom** | Un nom DNS interne (ex. `assistant.ansi.ne`) et un **certificat TLS** de l'autorité interne |
| **Utilisateurs visés** | 30 à 50 agents |

Le point décisif est le **GPU accessible depuis la VM**. Sans lui, l'application fonctionne mais
répond en 30 secondes ou plus au lieu de quelques secondes : elle ne serait pas utilisable au
quotidien.

---

## 2. Ce que fait l'application, et pourquoi elle a besoin d'un GPU

L'assistant répond aux questions des agents à partir des documents internes de l'ANSI. **Tout
s'exécute sur la machine** : le modèle de langage, la recherche documentaire, l'OCR des documents
scannés. Aucune donnée ne part vers un service extérieur, et le serveur n'a besoin d'aucun accès
Internet une fois installé.

Le temps de réponse dépend presque entièrement de la vitesse à laquelle le modèle **écrit** sa
réponse. Mesuré sur le poste de développement (processeur seul) : la recherche dans les documents
prend moins d'une seconde, la rédaction de la réponse prend 90 % du temps. C'est cette rédaction que
le GPU accélère.

---

## 3. La machine virtuelle

### 3.1 Caractéristiques

| Ressource | Recommandé | Minimum | Pourquoi |
|---|---|---|---|
| **GPU** | NVIDIA **L40S 48 Go** | NVIDIA **L4 24 Go** ou **A10 24 Go** | Le modèle retenu (§4) occupe ≈ 19 Go ; le reste sert aux questions traitées en parallèle |
| **vCPU** | 16 | 8 | L'OCR des documents scannés, PostgreSQL et l'API tournent sur le processeur |
| **RAM** | 64 Go | 32 Go | Chargement du modèle, PostgreSQL, OCR ; la RAM doit dépasser la taille du modèle |
| **Disque système** | 500 Go NVMe | 250 Go SSD | Système ≈ 30 Go, modèles ≈ 40 Go (deux versions, pour pouvoir revenir en arrière), corpus × 3 à 5 |
| **Volume de sauvegarde** | séparé, 500 Go | séparé, 250 Go | Sauvegarde quotidienne de la base et des documents, 30 jours conservés |
| **Système** | Ubuntu Server 24.04 LTS | Debian 12 | Paquets PostgreSQL 16, pgvector, Tesseract disponibles directement |

**Une seule VM suffit** pour 30 à 50 agents : l'application, la base et le modèle cohabitent. La
séparation en deux machines (application d'un côté, GPU de l'autre) ne deviendra utile qu'au-delà
de cette échelle.

### 3.2 Le GPU dans la VM : le point à vérifier en premier

La VM doit **voir la carte graphique directement**. Deux façons de le faire, selon l'hyperviseur :

| Hyperviseur | Méthode | Remarque |
|---|---|---|
| VMware ESXi | **PCI passthrough (DirectPath I/O)** de la carte vers la VM | La plus simple : la carte entière est dédiée à la VM |
| VMware ESXi | **NVIDIA vGPU** | Nécessite une licence NVIDIA AI Enterprise / vGPU ; inutile ici puisque la carte n'est pas partagée |
| Proxmox / KVM | **Passthrough PCIe (VFIO)** | IOMMU à activer dans le BIOS et sur l'hôte |
| Serveur physique | — | Rien de particulier |

Contraintes liées au passthrough :
- la **RAM de la VM doit être entièrement réservée** (pas de surallocation) ;
- la VM **ne peut pas être migrée à chaud** vers un autre hôte ;
- les cartes L40S, L4 et A10 sont des cartes de **centre de données** (refroidissement passif) : elles
  supposent un serveur rack prévu pour elles. Une carte grand public (RTX 4090) fonctionne mais n'est
  ni certifiée pour un serveur rack ni prise en charge en vGPU.

**Test de réception :** dans la VM, `nvidia-smi` doit afficher la carte et sa mémoire.

### 3.3 Pilote NVIDIA

Un pilote NVIDIA récent (branche 550 ou supérieure) dans la VM. Le serveur n'ayant pas Internet, le
pilote sera fourni dans le colis d'installation (fichier `.run` ou paquets `.deb`), sauf si l'équipe
infrastructure dispose d'un dépôt interne.

---

## 4. Le modèle de langage

### 4.1 Le nombre d'agents ne décide pas de la taille du modèle

Deux choses différentes sont souvent confondues :

| | Ce qui la détermine |
|---|---|
| **La qualité des réponses** | La taille et la qualité du modèle |
| **Le nombre d'agents servis en même temps** | La mémoire du GPU et le nombre de questions traitées en parallèle |

30 à 50 agents ne posent pas 50 questions à la même seconde. En pratique, on peut attendre **3 à 8
questions simultanées aux heures chargées** — c'est une estimation, à confirmer par un test de charge
(§8). C'est la **mémoire du GPU** qui permet de les traiter en parallèle, pas un modèle plus gros.

### 4.2 Un modèle plus gros n'est pas plus rapide

**C'est l'inverse** : à matériel égal, un modèle plus gros lit plus de paramètres pour chaque mot
écrit, et répond donc **plus lentement**. Ce qui rend l'assistant rapide :

1. **le GPU** — chaque mot est produit beaucoup plus vite que sur processeur ;
2. **un modèle qui ne raisonne pas avant de répondre** — le modèle actuel (`qwen3:4b`) écrit environ
   mille mots de raisonnement jetés pour une réponse de vingt. Sa variante sans raisonnement
   (`qwen3:4b-instruct-2507`) donne la même exactitude en **4,9 s au lieu de 34 s** (mesuré le 28/09) ;
3. **un modèle à experts (MoE)** — voir ci-dessous.

### 4.3 Le modèle recommandé : Qwen3-30B-A3B-Instruct-2507

C'est un modèle de **30 milliards de paramètres** dont **seulement 3 milliards travaillent pour
chaque mot écrit** (architecture « mixture of experts »). Il réunit les deux avantages :

| | `qwen3:4b-instruct-2507` (actuel recommandé) | **Qwen3-30B-A3B-Instruct-2507** | Modèle dense 32B |
|---|---|---|---|
| Qualité | correcte | **nettement meilleure** | meilleure |
| Paramètres actifs par mot | 4 milliards | **3 milliards** | 32 milliards |
| Vitesse sur GPU | rapide | **rapide, comparable** | ≈ 8 à 10 fois plus lent |
| Mémoire GPU (quantifié Q4) | ≈ 3 Go | **≈ 19 Go** | ≈ 20 Go |
| Raisonne avant de répondre | non | **non** | selon la variante |

Il tient sur une carte de 24 Go, mais **juste** : il reste peu de place pour les questions en
parallèle. Avec **48 Go**, il reste près de 30 Go pour traiter confortablement plusieurs agents à la
fois, ou pour essayer plus tard un modèle plus grand sans changer de machine. C'est la raison du
choix L40S au §3.1.

**Ce choix reste à confirmer par la mesure sur la machine cible.** Le changement de modèle ne touche
pas au code : il tient en deux lignes de configuration. Avant de le valider, on rejoue le jeu
d'évaluation (exactitude, sources, refus) et les sondes de sécurité — une consigne tenue par un
modèle ne l'est pas forcément par le suivant. `qwen3:4b-instruct-2507` reste dans le colis comme
**solution de repli** immédiate.

Alternative si le français doit primer : **Mistral Small 3.2 (24B)**, très bon en français, mais
dense, donc plus lent que le modèle à experts.

Le modèle de recherche documentaire (`embeddinggemma`, < 1 Go) ne change pas : le remplacer
obligerait à réindexer tous les documents.

---

## 5. Réseau et sécurité

### 5.1 Flux

| Sens | Port | Source / destination | Usage |
|---|---|---|---|
| Entrant | **443/tcp** | Postes des agents (réseau interne) | Application (HTTPS) |
| Entrant | 80/tcp | Postes des agents | Redirection vers 443 |
| Entrant | 22/tcp | **Sous-réseau d'administration uniquement** | Administration SSH |
| Sortant | 53, 123 | DNS et NTP **internes** | Résolution de noms, heure |
| Sortant | — | **Tout le reste refusé**, Internet compris | |

Le modèle (port 11434) et la base PostgreSQL (port 5432) écoutent **uniquement en local**
(`127.0.0.1`) : ils ne sont jamais exposés au réseau, et aucune règle de pare-feu ne doit les ouvrir.

L'absence de trafic sortant est **vérifiée par un test automatique** du projet
(`tests.isolation_probe`), exécuté sur le serveur lui-même après installation.

### 5.2 Ce que l'application protège déjà

Authentification par adresse professionnelle, mots de passe hachés (Argon2), sessions révocables,
cloisonnement des documents par rôle et par service appliqué **avant** la recherche, journal d'audit,
limitation des tentatives de connexion. Le détail est dans
[`docs/ARCHITECTURE_TECHNIQUE.md`](../docs/ARCHITECTURE_TECHNIQUE.md).

### 5.3 À chiffrer

La base contient les documents internes et l'historique des conversations : **le disque de la VM et
le volume de sauvegarde doivent être chiffrés au repos.**

---

## 6. Ce qui est demandé à l'équipe infrastructure

- [ ] La VM décrite au §3.1, avec le **GPU visible** (`nvidia-smi` fonctionne)
- [ ] Ubuntu Server 24.04 LTS installé, à jour au moment de la livraison
- [ ] Un compte administrateur (`sudo`) pour l'installation
- [ ] Un **nom DNS interne** pour l'application
- [ ] Un **certificat TLS** et sa clé, émis par l'autorité de certification interne, pour ce nom
- [ ] Les règles de pare-feu du §5.1
- [ ] Un **moyen de transférer le colis d'installation** (≈ 30 Go la première fois, surtout les
      modèles) : support amovible contrôlé ou dépôt interne, selon la procédure de l'ANSI
- [ ] Un **volume de sauvegarde** séparé, chiffré
- [ ] Le **chiffrement du disque** de la VM
- [ ] Si elle existe : l'intégration à la **supervision** de l'ANSI (disponibilité, disque, mémoire GPU)
- [ ] Pour plus tard : un accès en lecture à l'**annuaire** (LDAP / Active Directory), pour
      remplacer les comptes locaux par les comptes de l'agence

---

## 7. Configuration retenue côté application

Pour information de l'équipe infrastructure ; le détail est dans
[`DEPLOYMENT_ON_ANSI_SERVERS.md`](DEPLOYMENT_ON_ANSI_SERVERS.md).

| Composant | Rôle | Écoute |
|---|---|---|
| nginx | HTTPS, fichiers de l'interface | 443 (réseau interne) |
| API FastAPI (service systemd) | Logique, droits, recherche | 127.0.0.1:8000 |
| PostgreSQL 16 + pgvector | Comptes, documents, index | 127.0.0.1:5432 |
| Ollama (service systemd) | Exécution des modèles sur le GPU | 127.0.0.1:11434 |
| Tesseract | OCR des PDF scannés, en local | — |

Réglages d'Ollama pour le service :

```ini
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"          # garder le modèle chargé
Environment="OLLAMA_MAX_LOADED_MODELS=2"     # modèle de réponse + modèle de recherche
Environment="OLLAMA_NUM_PARALLEL=4"          # questions traitées en parallèle
Environment="OLLAMA_FLASH_ATTENTION=1"       # réduit la mémoire par question
```

`OLLAMA_NUM_PARALLEL` se règle **après** le test de charge (§8) : chaque question en parallèle
consomme de la mémoire GPU.

Fichiers de langue de l'OCR, installés par les paquets `tesseract-ocr-fra` et `tesseract-ocr-eng`,
à déclarer dans la configuration de l'application :

```ini
TESSERACT_CMD=/usr/bin/tesseract
TESSDATA_DIR=/usr/share/tesseract-ocr/5/tessdata
```

---

## 8. Recette : ce qui sera vérifié une fois la machine livrée

Rien n'a encore été mesuré sur GPU : tous les chiffres du projet viennent d'un portable sans GPU. Ces
mesures sont le premier travail à faire, et elles valident (ou corrigent) les choix de ce document.

| Vérification | Comment | Attendu |
|---|---|---|
| GPU visible dans la VM | `nvidia-smi` | La carte et sa mémoire s'affichent |
| Modèle exécuté sur GPU | `ollama ps` | `100% GPU` |
| Rien ne sort de la machine | `tests.isolation_probe` | Seules des adresses locales contactées |
| Qualité des réponses | `tests.evaluate` | Pas de recul par rapport au relevé de référence (34/34) |
| Sécurité | `tests.security_probe`, `tests.prompt_probe` | 17/17 et 7/7 |
| Temps de réponse | `tests.evaluate` (latence médiane) | Quelques secondes |
| Charge | 5, 10 puis 20 questions simultanées | Temps de réponse et mémoire GPU relevés |
| Sauvegarde | Restauration sur une autre machine | Application fonctionnelle après restauration |

Le résultat du test de charge fixe `OLLAMA_NUM_PARALLEL` et confirme si la carte de 24 Go suffit ou
si la carte de 48 Go est nécessaire.

---

## 9. Avant d'importer des documents réels

Deux décisions appartiennent à la direction de l'ANSI, pas à la technique, et conditionnent l'usage
de documents réels :

1. **Durée de conservation** des conversations et du journal d'audit.
2. **Indexer ou non les dossiers individuels** (RH notamment) : une consigne donnée au modèle n'est
   pas un contrôle d'accès.

Jusqu'à ces décisions, seuls des documents non sensibles ou anonymisés seront importés.

---

## 10. Évolution

| Si… | Alors |
|---|---|
| Le nombre d'agents dépasse ≈ 100, ou les questions simultanées dépassent ce que le GPU absorbe | Second GPU, ou passage d'Ollama à vLLM (meilleur traitement en parallèle ; demande une adaptation du code) |
| Un modèle plus grand est souhaité | La carte de 48 Go permet d'essayer un modèle dense de 24B à 32B sans changer de machine — plus lent, à mesurer |
| L'application et le modèle doivent être séparés | Deux VM : application + base (4 vCPU, 16 Go) d'un côté, modèle + GPU de l'autre |
