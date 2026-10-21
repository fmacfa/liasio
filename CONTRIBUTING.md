# Contribuer à LinkVault

Merci de contribuer avec des problèmes reproductibles et des modifications ciblées. Pour une vulnérabilité, utilisez le canal privé décrit dans [SECURITY.md](SECURITY.md).

## Développement

Suivez le démarrage local du README. Les dépendances Python sont gérées par uv et verrouillées dans `uv.lock`. Il n’y a pas de compilation frontend, de CDN ni de framework JavaScript à installer.

```sh
uv sync --frozen
uv run --env-file .env python manage.py test --noinput
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pip-audit --local
```

Pour modifier le modèle, générez une migration et incluez-la dans la PR. Les tests utilisent une base séparée. Pour tester PostgreSQL, fournissez les variables `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` pointant vers un serveur de test où le compte peut créer la base `test_<nom>`.

## Structure

| Dossier | Rôle |
| --- | --- |
| `linkvault/` | Configuration, URL, WSGI |
| `vault/models.py` | Comptes, dossiers, fichiers, liens, invitations, événements |
| `vault/storage.py` | Stockage, antivirus, quota, suppression et réservation des téléchargements |
| `vault/security.py` | Capacités HMAC et limitations de fréquence |
| `vault/views.py`, `forms.py` | Parcours et validation des données |
| `vault/tests/` | Tests fonctionnels et de concurrence |
| `templates/`, `static/` | HTML échappé, CSS, JavaScript progressif |
| `docker/`, `compose.yaml` | Déploiement autonome |
| `docs/` | Exploitation, sécurité, comparatif, captures fictives |

## Principes

- Contrôler les droits côté serveur à chaque accès ; tester les accès entre comptes.
- Ne jamais introduire de `csrf_exempt`, d’HTML utilisateur non échappé ou de URL publique de blob.
- Garder les règles de quota et de consommation transactionnelles, avec l’ordre de verrouillage utilisateur → dossier → lien/fichier.
- Préférer une amélioration ciblée à l’ajout d’un service obligatoire.
- Décrire les limites observables sans promesse de sécurité absolue.
- Vérifier les formulaires et la page reçue sur desktop et mobile. Respecter les préférences de mouvement réduit et l’usage clavier.
- Ne jamais committer secrets, bases, uploads ou données réelles. Les captures utilisent des données fictives.

Les contributions sont proposées sous MIT. Aucun CLA n’est demandé par ce dépôt.
