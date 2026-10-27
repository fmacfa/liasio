# Contribuer à Liasio

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
| `docs/` | Exploitation, sécurité, comparatif, captures réelles avec données de démonstration |

## Principes

- Contrôler les droits côté serveur à chaque accès ; tester les accès entre comptes.
- Garder CSRF sur tous les parcours à session. Seules les routes API v1, authentifiées exclusivement par `Authorization: Bearer` et sans autorité des cookies, utilisent `csrf_exempt`. Ne jamais exposer de HTML utilisateur non échappé ou de URL publique de blob.
- Garder les règles de quota et de consommation transactionnelles, avec l’ordre de verrouillage utilisateur → dossier → lien/fichier. Une admission reprenable prend d’abord le verrou global de capacité, puis Instance, puis utilisateur ; ne jamais prendre ce verrou global en tenant un verrou utilisateur. Les imports classiques et ZIP prennent leur réservation avant leur transaction métier.
- Préférer une amélioration ciblée à l’ajout d’un service obligatoire.
- Décrire les limites observables sans promesse de sécurité absolue.
- Vérifier les formulaires et la page reçue sur desktop et mobile. Respecter les préférences de mouvement réduit et l’usage clavier.
- Ne jamais committer secrets, bases, uploads ou données réelles. Les captures utilisent des données fictives.

Les contributions sont proposées sous MIT. Aucun CLA n’est demandé par ce dépôt.

## Traductions et guides

Les messages source sont en français, les catalogues anglais et allemands dans `locale/{en,de}/LC_MESSAGES/`. Utilisez `gettext_lazy` pour les champs et `translate` / `blocktranslate` dans les templates ; gardez les états métier et identifiants API indépendants des traductions. Les textes JavaScript passent par `gettext` et `interpolate`, servis par `/jsi18n/`. Installez GNU gettext pour extraire et compiler :

```sh
uv run --env-file .env python manage.py makemessages -l en -l de --ignore=.venv --ignore=.local --ignore=staticfiles --ignore=vault/migrations --ignore=vault/tests
uv run --env-file .env python manage.py makemessages -l en -l de -d djangojs --ignore=.venv --ignore=.local --ignore=staticfiles
uv run --env-file .env python manage.py compilemessages -l en -l de --ignore=.venv --ignore=.local
uv run python scripts/check_documentation.py
```

Incluez `.po` et `.mo`, sans entrée vide ou fuzzy. Les guides de `docs/guide/{fr,en,de}/` sont lus directement par l’application et par GitHub. Mettez à jour les trois langues et les captures locales réelles dans `docs/screenshots/user-guide/`. Utilisez exclusivement des données de démonstration, aucun secret visible. Le vérificateur CI contrôle les liens, images, catalogues compilés et paramètres de traduction.
