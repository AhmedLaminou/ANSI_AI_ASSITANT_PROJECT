# Plan de test — POC Assistant IA ANSI

## Préconditions

- `qwen3:4b` et `embeddinggemma` sont installés dans Ollama.
- L'API et le frontend sont démarrés localement.
- Les documents de test ne sont ni confidentiels ni personnels.

## Tests fonctionnels

1. Importer un TXT simple contenant une date, un responsable et un objectif.
2. Poser une question dont la réponse figure explicitement dans le document.
3. Vérifier que la réponse cite le document et la page.
4. Poser une question absente du document ; l'assistant doit reconnaître l'absence d'information.
5. Importer deux documents contradictoires ; vérifier que la réponse indique les sources plutôt que de trancher sans preuve.

## Tests de droits

1. Créer un utilisateur `user` dans l'interface Administration.
2. Importer un document réservé à `admin`.
3. Se reconnecter avec l'utilisateur `user` : le document ne doit ni apparaître dans sa liste, ni être retrouvé par le chat.

## Test offline

1. Vérifier que les deux modèles sont entièrement téléchargés.
2. Couper Wi-Fi et Ethernet.
3. Se connecter, rechercher dans un document déjà indexé et poser une question.
4. La réponse et les sources doivent toujours être disponibles.

## Limites connues du POC

- Pas d'OCR : les PDF composés uniquement d'images sont refusés.
- SQLite et recherche en mémoire conviennent au POC, pas à la production multi-utilisateur.
- Les réponses doivent être évaluées par un humain avant toute décision administrative.
