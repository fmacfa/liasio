# Installer, sauvegarder et maintenir

## Topologie de production

```text
Internet :443 → Caddy → Gunicorn / Django → PostgreSQL (réseau privé)
                                      → fichiers (volume Docker privé)
                                      → ClamAV (réseau privé)
                        maintenance toutes les 5 minutes → purge des métadonnées et orphelins
```

Seuls Caddy et les ports 80/443 sont accessibles depuis Internet. ClamAV dispose d’une sortie réseau pour mettre à jour ses signatures. Le service applicatif sort sur le réseau frontal pour le SMTP éventuel. Le serveur doit avoir suffisamment de disque pour les fichiers, les ZIP temporaires, la base et les sauvegardes.

Visez 8 Go de RAM pour démarrer avec la pile complète. La limite de ClamAV est 4 Go, en accord avec les [recommandations de son image officielle](https://docs.clamav.net/manual/Installing/Docker.html). Les mises à jour de signatures peuvent augmenter temporairement la consommation.

## Démarrage et diagnostic

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 clamav
docker compose exec app python manage.py check --deploy --fail-level ERROR
```

`check --deploy` peut émettre les avertissements Django sur HSTS sous-domaines/préchargement : ils ne sont pas activés pour ne pas imposer HTTPS à d’autres services de votre domaine. Les cookies sécurisés, le HTTPS, le secret, l’antivirus et le proxy doivent être configurés. Ne rendez pas le port 8000 public.

Une indisponibilité de ClamAV bloque les nouveaux imports. Les fichiers déjà analysés restent téléchargeables. Si un fichier de démonstration non analysé est transporté en production, l’application refuse de créer un lien ou de le télécharger ; importez-le à nouveau dans un dossier avec l’antivirus fonctionnel.

En cas de réponse 413, vérifiez les tailles dans `.env` et Caddy. Les limites de ClamAV doivent aussi être cohérentes. Ne désactivez pas l’antivirus pour contourner une limite ; configurez le dimensionnement voulu.

## Gestion des comptes

```sh
# Modifier un quota individuel ; ne descend pas sous l’usage actuel.
docker compose exec app python manage.py set_quota personne@example.com 10

# Récupération sans SMTP ; saisie interactive du mot de passe, pas d’argument secret.
docker compose exec app python manage.py changepassword personne@example.com

# Ajouter un administrateur depuis le serveur, avec saisie interactive.
docker compose exec app python manage.py createsuperuser

# Effacement définitif d’un membre et de ses fichiers (administrateurs exclus).
docker compose exec app python manage.py erase_user personne@example.com --confirm
```

L’administrateur web peut suspendre les membres et révoquer les invitations. Chaque membre gère ses propres fichiers sur la page Stockage. Le tableau d’administration affiche les volumes agrégés par compte, sans permettre de lire leurs documents.

## Sauvegarder

Les sauvegardes doivent contenir **la base PostgreSQL**, **le volume de fichiers** et une copie séparée et chiffrée de **`.env`**. Conservez la même version du code/commit. Ne placez jamais les sauvegardes dans le dépôt.

Le script ci-dessous arrête les écritures applicatives et la maintenance pendant l’export puis les relance, y compris si une commande échoue :

```sh
scripts/backup-and-verify.sh
```

Il écrit `backups/<date-UTC>/database.sql` et `files.tar.gz`, ainsi que `checksums.json`. Les archives incluent la corbeille et les envois temporaires. Ces fichiers contiennent des données privées, ne sont **pas chiffrés** par le script et doivent être chiffrés puis copiés sur un autre support avec votre solution de sauvegarde. Testez régulièrement la restauration. Ne supprimez pas les sauvegardes après une simple vérification d’existence.

La page Stockage mesure le volume contenant `/data/files`. Si PostgreSQL ou les sauvegardes résident sur un autre disque, contrôlez aussi leur capacité. Un quota est une limite logique, pas une réservation de disque.

## Restaurer sur une installation vide

Ces commandes supposent une **nouvelle installation sans données à préserver**, la bonne version du code et votre `.env` récupéré de manière sûre. Une restauration écrase l’état d’une cible ; ne l’exécutez pas sur votre instance active.

```sh
docker compose build app
docker compose up -d db
# Attendre que db soit healthy dans docker compose ps.
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U linkvault -d linkvault < backups/DATE/database.sql
docker compose run -T --rm --no-deps --entrypoint tar app -xzf - -C /data < backups/DATE/files.tar.gz
docker compose up -d
docker compose ps
```

Vérifiez la connexion, la liste des fichiers, le quota, un téléchargement de test, l’expiration et un import analysé. Restaurez la base et les fichiers issus de la **même sauvegarde**. Si le domaine change, mettez `DOMAIN` et `APP_URL` à jour ; les liens utilisant l’ancien domaine nécessitent une redirection externe maîtrisée ou une nouvelle diffusion.

Les sauvegardes peuvent contenir des données supprimées depuis. Leur durée de conservation et leur effacement doivent être organisés par l’exploitant.

## Mise à jour

```sh
scripts/backup-and-verify.sh
git pull --ff-only
docker compose pull
docker compose build --pull app
docker compose up -d
docker compose ps
```

L’application lance les migrations avant Gunicorn. Le lockfile Python et les empreintes des images fixent les dépendances. Les mises à jour des empreintes doivent être revues et testées régulièrement ; télécharger une empreinte inchangée ne récupère pas les nouveaux correctifs du fournisseur. Dependabot suit les images du Dockerfile ; vérifiez aussi les images de services dans Compose et la CI. Relisez les notes de version et testez les mises à jour avant une instance importante.

Pour le renforcement après 0.4.0, arrêtez `webhooks` avant la migration `0006`, puis redémarrez la pile complète. Anciens et nouveaux processus ne doivent pas fonctionner ensemble. Les aperçus exigent désormais Landlock ABI 3+ sur Linux : en son absence, ils restent indisponibles sans bloquer les téléchargements. Les guides d’[automatisation](guide/fr/automation.md) et d’[exploitation](guide/fr/operations.md) précisent les changements du client d’envoi et de la file de livraison.

## Maintenance et secrets

Le conteneur `maintenance` exécute la purge toutes les cinq minutes : sessions expirées, limites de fréquence, anciennes invitations, événements au-delà de la rétention, fichiers orphelins de plus de 24 h. Une expiration de lien ne détruit **pas** les fichiers, qui peuvent être utilisés par d’autres liens.

Ne changez pas `SECRET_KEY` à chaque déploiement : sa rotation invalide sessions, invitations et liens de partage. Après suspicion de fuite, changez-la volontairement, redémarrez les services et rediffusez de nouveaux liens. Gardez une politique de sauvegarde des secrets indépendante de celle du dépôt public.

## Autre reverse proxy

Si vous remplacez Caddy, maintenez HTTPS et HSTS, la limite de corps, les délais de réception, le remplacement des en-têtes proxy et l’isolation réseau de Gunicorn. N’activez pas de journalisation des URL de capacités. Ne placez pas un cache public devant les pages privées ou les téléchargements. N’ajoutez pas `null` ou des origines génériques à `CSRF_TRUSTED_ORIGINS`.

## Limites de la version 0.4

- Déploiement sur un seul hôte, stockage local ; pas de haute disponibilité. API privée à clés et permissions ; aucun import anonyme.
- 1 Gio par fichier / 5 Gio par dossier par défaut ; reprise pendant 24 h en resélectionnant les mêmes fichiers.
- ZIP global uniquement ; galerie d’images réduites et première page des PDF, sans prévisualisation active des originaux. Voir [les aperçus](PREVIEWS.md).
- Pas de chiffrement de bout en bout, SSO ou vérification de boîte e-mail indépendante de l’invitation.
- Notifications d’ouverture par webhooks signés ; aucune mesure de réception complète.
- La corbeille est conservée 30 jours ; après purge définitive, seule une sauvegarde permet la restauration.


## Guide de fiabilité 0.2

[RELIABILITY.md](RELIABILITY.md) décrit en anglais les nouvelles options, la MFA, les alertes, le contrôle de restauration isolé et les tests de charge. `MFA_ENCRYPTION_KEY` doit rester stable ; en son absence, la clé MFA dépend de `SECRET_KEY`. Sauvegardez cette configuration avant toute rotation. La commande de récupération du mot de passe ne désactive pas la MFA.

## Automatisations et traductions (0.4)

La pile complète comprend le service `webhooks`, avec deux processus, accès à PostgreSQL et au réseau HTTPS public, sans volume d’originaux. Vérifiez `docker compose logs --tail=50 webhooks` et la page Automatisations. Pour une installation manuelle, superviser `python manage.py deliver_webhooks --loop --workers 2`. Les comptes alternent et ne peuvent occuper qu’un envoi simultané chacun. Une file d’événements ne livre rien sans ce service.

Le chiffrement des destinations, secrets de signature et reçus d’idempotence utilise la même clé stable que la MFA. Préservez `MFA_ENCRYPTION_KEY` (ou `SECRET_KEY` si la clé MFA est absente) lors d’une restauration. Les clés API deviennent invalides après changement de mot de passe/MFA. Les reçus API expirent après 24 h, les livraisons après 30 jours via `housekeeping`.

Les catalogues `.mo` sont inclus. Exécutez `collectstatic` après chaque mise à jour, y compris pour les captures des guides. Les utilisateurs choisissent FR/EN par cookie ; les titres, noms de fichiers et présentations restent inchangés. [Guide français](guide/fr/README.md) · [API et webhooks](API.md).
