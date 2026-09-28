# Plan de test — Assistant IA ANSI

Procédure **manuelle**, à dérouler dans l'interface avant une démonstration ou une mise en service.
Elle complète les contrôles automatiques, qui la précèdent :

```powershell
cd backend;  .\.venv\Scripts\python.exe -m pytest tests/ -q     # 359 contrôles, sans modèle
cd frontend; npm test                                           # 34 tests d'interface, dont l'accessibilité
```

## Préconditions

- `qwen3:4b` et `embeddinggemma` sont installés dans Ollama.
- L'API et le frontend sont démarrés localement.
- Les documents de test ne sont ni confidentiels ni personnels.

## Tests fonctionnels

1. Importer un TXT simple contenant une date, un responsable et un objectif.
2. Poser une question dont la réponse figure explicitement dans le document.
3. Vérifier que la réponse cite le document et la page.
4. Poser une question absente du document ; l'assistant doit reconnaître l'absence d'information,
   indiquer **qui contacter**, et la question doit apparaître dans **Administration → Lacunes**.
5. Poser une question de suite courte (« et pour un stagiaire ? ») : elle doit être comprise avec la
   précédente.
6. Publier une bonne réponse comme **réponse validée**, puis reposer la question : la réponse doit
   arriver instantanément, signée.
7. Importer deux documents contradictoires ; vérifier que la réponse indique les sources plutôt que
   de trancher sans preuve. *(Aucun jeu de données automatique ne couvre encore ce cas.)*
8. Importer un PDF scanné : son texte doit être reconnu localement (OCR).

## Tests de droits

1. Créer un compte `user` du service RH et un compte `user` du service finances.
2. Importer un document du service finances, et un document réservé à `admin`.
3. Se connecter avec le compte RH : aucun des deux documents ne doit apparaître dans sa liste, ni
   être retrouvé par le chat, la recherche seule ou une question de suite.
4. Se connecter avec le compte finances : le document finances est visible, le document `admin` non.
5. Demander « à quoi ai-je accès ? » avec chaque compte : la réponse doit correspondre à ce qui
   précède.

## Tests d'authentification

1. Déposer une demande d'accès avec une adresse professionnelle ; elle apparaît chez
   l'administrateur, et le compte ne peut pas se connecter avant approbation.
2. Approuver en attribuant un autre service que celui demandé : c'est celui de l'administrateur qui
   s'applique.
3. À la première connexion d'un compte créé par l'administrateur, le changement de mot de passe est
   imposé avant tout autre écran.
4. « Mot de passe oublié » : la demande apparaît chez l'administrateur ; le mot de passe de l'agent
   n'a pas changé.
5. Ouvrir une session dans deux navigateurs, changer le mot de passe dans l'un : l'autre revient à
   l'écran de connexion à sa prochaine action.

## Test offline

1. Vérifier que les deux modèles sont entièrement téléchargés.
2. Couper Wi-Fi et Ethernet.
3. Se connecter, rechercher dans un document déjà indexé et poser une question.
4. La réponse et les sources doivent toujours être disponibles.

`tests/isolation_probe.py` fait la même vérification automatiquement, sur le trafic réellement émis.

## Limites connues

- L'OCR restitue du texte brut : un tableau scanné devient une suite de mots, pas des colonnes.
- SQLite et la recherche en mémoire conviennent au développement, pas à la production
  multi-utilisateur : PostgreSQL + pgvector est disponible et doit être activé.
- Les réponses doivent être évaluées par un humain avant toute décision administrative.
