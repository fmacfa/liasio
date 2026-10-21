# LinkVault

**Vos fichiers, votre domaine, vos conditions.**

LinkVault est une application open source de partage de fichiers à installer sur votre serveur. Constituez des dossiers, personnalisez leur présentation et créez plusieurs liens indépendants avec mot de passe, expiration ou limite de téléchargements.

Interface en français · Licence **MIT** · Python / Django · Docker Compose

> **Version 0.1 — première version fonctionnelle.** Les contrôles de sécurité sont testés, mais le projet n’a pas fait l’objet d’un audit de sécurité indépendant. Il n’offre pas encore de chiffrement de bout en bout ni de double authentification.

![Tableau de bord LinkVault, avec des données fictives](docs/screenshots/dashboard.png)

## Ce que vous pouvez faire

| Besoin | Disponible dans la version 0.1 |
| --- | --- |
| Première connexion | Assistant protégé par une clé d’installation, création du compte administrateur avec e-mail et mot de passe |
| Plusieurs comptes | Invitations personnelles, valables 7 jours, usage unique, révocables ; comptes membres isolés |
| Bibliothèque | Import de fichiers ou d’un dossier avec son arborescence, titre, présentation et message au destinataire |
| Page Importer | Choix du dossier, liste des fichiers et tailles, barre de progression réelle, état de vérification après transfert |
| Page de partage | Nom, signature, palette de couleurs et pied de page propres à chaque compte ; aperçu en direct |
| Plusieurs liens par dossier | Nom interne, mot de passe facultatif, date d’expiration, nombre maximal de téléchargements |
| Usage unique | Un seul téléchargement autorisé, y compris en cas de requêtes simultanées |
| Suivi | Ouvertures et téléchargements autorisés datés, filtre par lien, export CSV |
| Stockage | Nombre de fichiers, tailles, quotas, recherche, tri par taille/nom/date, suppression individuelle ou multiple avec confirmation |
| Administration du stockage | Volume disque disponible, total de fichiers, consommation par compte ; quotas ajustables en ligne de commande |
| Gestion des accès | Suspension d’un membre, révocation de ses liens et sessions ; changement et récupération du mot de passe |
| Exploitation | HTTPS automatique avec Caddy, PostgreSQL, antivirus ClamAV, maintenance horaire, sauvegarde/restauration documentée |

**Un dossier n’est stocké qu’une fois.** Vous pouvez créer un lien pour un client avec un mot de passe, un autre qui expire demain et un troisième utilisable une fois.

### Ce que signifie « ouvert » ou « téléchargé »

- **Page ouverte** : la page autorisée a été servie au navigateur. Les ouvertures d’une même session sont dédupliquées pendant 30 minutes. Un robot de prévisualisation peut aussi ouvrir une page.
- **Téléchargement autorisé** : le serveur a préparé le ZIP et réservé une utilisation avant de l’envoyer. Cela ne prouve pas la réception complète du fichier. Un transfert interrompu consomme l’utilisation ; le propriétaire peut créer un nouveau lien.
- **Usage unique** : ouvrir la page ne consomme rien. Le bouton de téléchargement effectue un POST protégé par CSRF. Une réservation atomique en base empêche deux téléchargements simultanés de dépasser la limite.
- **Révocation** : bloque les prochaines autorisations. Elle ne rappelle pas les fichiers déjà reçus et n’interrompt pas nécessairement un transfert déjà autorisé.

## Installer sur votre domaine

Prévoir un serveur Linux avec **Docker Engine et Docker Compose v2**, Git et Python 3 pour la configuration. Pour la pile complète, prévoir **8 Go de RAM** (dont jusqu’à 4 Go pour ClamAV), 2 vCPU et un disque adapté à vos fichiers. Ce dimensionnement est un point de départ, pas un résultat de test de charge.

1. Faites pointer l’enregistrement DNS A, et AAAA si utilisé, de votre domaine vers le serveur.
2. Autorisez les ports entrants **80 et 443**. La base, l’application et l’antivirus ne doivent pas être exposés directement.
3. Installez le dépôt et générez des secrets uniques :

```sh
git clone https://github.com/fmacfa/linkvault.git
cd linkvault
python3 scripts/configure.py --domain fichiers.example.com
docker compose up -d --build
docker compose ps
```

4. Attendez que ClamAV soit prêt ; son premier démarrage peut prendre plusieurs minutes.
5. Ouvrez `https://fichiers.example.com`, puis créez votre compte avec la valeur **SETUP_TOKEN** de votre fichier `.env`, votre nom, votre adresse e-mail et votre mot de passe.
6. Créez un dossier, importez les fichiers, puis choisissez **Créer un lien**.

Le script crée `.env` avec des permissions `0600` et refuse de l’écraser. Le fichier contient des secrets : ne le publiez jamais. Après l’initialisation, la création du premier compte est fermée même si la clé est encore présente.

Caddy obtient et renouvelle le certificat TLS. Si vous utilisez un autre proxy, consultez le [guide d’exploitation](docs/OPERATIONS.md) avant de modifier la topologie.

## Essayer en local

Installer [uv](https://docs.astral.sh/uv/getting-started/installation/), puis :

```sh
git clone https://github.com/fmacfa/linkvault.git
cd linkvault
python3 scripts/configure.py --dev
uv sync --frozen
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py runserver 127.0.0.1:8000
```

Ouvrez [http://127.0.0.1:8000](http://127.0.0.1:8000). La clé de première installation se trouve dans `.env`. Ce mode utilise SQLite, HTTP et un antivirus désactivé explicitement. Il est réservé à la machine locale et aux fichiers de démonstration.

Pour créer des données fictives sur une **installation locale vide**, choisissez un mot de passe de démonstration et exécutez :

```sh
DEMO_PASSWORD='remplacez-par-votre-phrase-de-demo' uv run --env-file .env python manage.py seed_demo
```

Le compte est `demo@example.com`. Cette commande refuse de s’exécuter en production ou si un compte existe déjà. Ne réutilisez pas les données ou secrets de démonstration pour votre serveur public.

## Paramètres principaux

Toutes les valeurs sont configurées dans `.env` ; un modèle est fourni dans [.env.example](.env.example).

| Paramètre | Valeur par défaut / usage |
| --- | --- |
| `APP_URL` / `DOMAIN` | Origine HTTPS canonique, sans chemin / nom de domaine |
| `SECRET_KEY` | Secret aléatoire d’au moins 50 caractères ; signe sessions et capacités |
| `SETUP_TOKEN` | Secret d’au moins 32 caractères pour le premier compte |
| `MAX_FILE_BYTES` | 104857600, soit 100 Mio par fichier |
| `MAX_UPLOAD_BYTES` | 524288000, soit 500 Mio par envoi **et par dossier** |
| `DEFAULT_QUOTA_BYTES` | 5368709120, soit 5 Gio pour chaque nouveau compte |
| `EVENT_RETENTION_DAYS` | 90 jours d’historique d’activité et d’administration |
| `TIME_ZONE` | `Europe/Berlin`, dates stockées en UTC et affichées dans ce fuseau |
| `ANTIVIRUS_REQUIRED` | `true` en production ; les imports échouent si l’analyse ne peut pas aboutir |
| `CLAMAV_HOST` | `clamav` dans Compose ; service privé, jamais exposé à Internet |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP facultatif pour récupérer un mot de passe |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | STARTTLS par défaut ; ne pas activer les deux simultanément |
| `DEFAULT_FROM_EMAIL` | Adresse d’expédition autorisée par votre serveur SMTP |
| `TRUST_PROXY` | `true` uniquement derrière le proxy isolé prévu dans Compose |

La limite de 100 fichiers par dossier est fixe dans cette version. Les formats acceptés sont définis dans `ALLOWED_EXTENSIONS`. Pour augmenter les tailles, ajustez aussi Caddy et les limites ClamAV ; voir le guide d’exploitation.

Les invitations créent un lien à copier et transmettre. Elles n’envoient pas d’e-mail automatiquement. Sans SMTP, l’administrateur du serveur peut réinitialiser un mot de passe via `manage.py changepassword`.

## Sécurité et confidentialité

- Mots de passe **Argon2id** ; minimum de 12 caractères, longueur maximale de 128 pour les formulaires web.
- Aucune inscription publique ; initialisation protégée, invitations signées, expirables et consommées une seule fois.
- Contrôle du propriétaire sur chaque ressource ; un administrateur web ne peut pas parcourir les fichiers d’un autre compte.
- Sessions serveur, cookies HttpOnly/Secure en production, protection CSRF, validation d’hôte, CSP restrictive, protection contre l’encapsulation.
- Capacités HMAC-SHA256 non déductibles des seuls identifiants de base ; mots de passe des liens hachés. Aucun jeton de partage en clair dans la base.
- Limitation des tentatives en base, partagée entre workers ; identité IP utilisée sous forme de HMAC pour cette limitation et purgée ensuite.
- Fichiers hors du répertoire web, noms physiques aléatoires, chemins relatifs validés, contrôle des extensions et des volumes.
- ClamAV obligatoire dans la pile de production ; réponse inattendue, virus, limite d’analyse ou indisponibilité bloquent l’import.
- Téléchargements forcés en pièce jointe ; les contenus HTML/SVG ne sont pas prévisualisés ou exécutés.
- Historique sans IP, géolocalisation ou identifiant publicitaire ; export CSV neutralisant les formules.
- La suppression de fichiers révoque leurs liens et libère le quota ; les blobs orphelins sont purgés après un délai de sécurité.

Les fichiers sont lisibles par l’administrateur du système d’exploitation : **ce n’est pas du chiffrement de bout en bout**. Utilisez un volume et des sauvegardes chiffrés si nécessaire. Un antivirus ne garantit pas l’absence de menace. Le contrôle d’extension n’est pas une validation complète du contenu de tous les formats acceptés. L’application n’est pas conçue pour des dépôts anonymes massifs.

[Politique de sécurité](SECURITY.md) · [Modèle de sécurité](docs/SECURITY_MODEL.md) · [Sauvegarde, restauration, maintenance](docs/OPERATIONS.md)

## Vérifier et contribuer

```sh
uv sync --frozen
uv run --env-file .env python manage.py test --noinput
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pip-audit --local
```

La suite couvre notamment les droits entre comptes, les limites d’import, les invitations, le CSRF, les mots de passe de liens, la traversée de chemins, la suppression et les accès simultanés. Le workflow GitHub teste SQLite et PostgreSQL, construit l’image et vérifie l’antivirus avec sa signature de test EICAR.

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour l’organisation du code et [ROADMAP.md](ROADMAP.md) pour les prochaines évolutions.

## Inspirations et positionnement

Nous avons étudié [Pingvin Share](https://github.com/stonith404/pingvin-share), [ProjectSend](https://github.com/projectsend/projectsend), [Gokapi](https://github.com/Forceu/Gokapi) et [Send](https://github.com/timvisee/send). Le [comparatif](docs/COMPARISON.md) décrit ce que ces projets apportent et les choix de LinkVault.

LinkVault privilégie les dossiers réutilisables, la présentation et des règles de liens explicites dans une application compacte. Cette version ne prétend pas remplacer toutes les fonctions de ces projets plus anciens. Le code applicatif et les éléments graphiques de LinkVault ont été créés pour ce projet ; aucun code de ces quatre projets n’a été repris.

## Licence

[MIT](LICENSE), copyright 2026 Filippo Maria Celi et contributeurs. Les dépendances et images de conteneurs conservent leurs propres licences. Voir [THIRD_PARTY.md](THIRD_PARTY.md).
