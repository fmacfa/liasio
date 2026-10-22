# Modèle de sécurité de LinkVault 0.3

## Frontières de confiance

- Le navigateur et les fichiers importés sont non fiables. Les titres, descriptions et noms restent du texte échappé.
- Un membre est autorisé à administrer ses propres ressources ; il n’obtient aucun droit sur celles des autres comptes, même en devinant leurs UUID.
- Un administrateur de l’application invite/suspend des comptes et voit leurs volumes de stockage. Les routes de bibliothèque vérifient toujours le propriétaire.
- L’administrateur du serveur et les opérateurs de sauvegarde peuvent lire fichiers et base. Chiffrer le disque/sauvegardes relève de l’hébergement ; aucun secret de chiffrement côté navigateur n’est implémenté.
- Le reverse proxy est fiable seulement s’il est isolé comme dans Compose. Il remplace `X-Real-IP` et `X-Forwarded-Proto`. Ne jamais exposer le port Gunicorn avec `TRUST_PROXY=true`.

## Comptes

Sur une instance neuve, la première inscription par e-mail et mot de passe crée l’administrateur, sans clé d’installation. Toute personne ayant accès à l’instance non initialisée peut revendiquer ce premier compte ; l’exploitant doit donc terminer l’assistant avant de diffuser l’adresse ou d’ouvrir l’accès à d’autres personnes. Cette inscription ne vérifie pas la maîtrise de la boîte e-mail. Un singleton précréé et verrouillé empêche deux créations administrateur simultanées. La présence d’un compte ou l’état initialisé ferme l’assistant, y compris après suppression du dernier compte. CSRF, limitation des tentatives et validation des mots de passe restent actifs. Les deux étapes ne transmettent les identifiants qu’à la validation finale ; aucun mot de passe n’est conservé dans le stockage du navigateur.

Les invitations lient une adresse fixée par l’administrateur à une capacité signée. Leur acceptation consomme la ligne dans une transaction. Elles ne prouvent pas la maîtrise de la boîte e-mail si l’administrateur transmet le lien à la mauvaise personne. La vérification e-mail dédiée est prévue dans la roadmap.

Les mots de passe utilisent Argon2id via Django. La réinitialisation SMTP utilise les jetons Django, valables une heure et invalidés après changement. Un changement invalide les autres sessions ; une suspension supprime aussi les sessions et révoque les liens. Le dernier administrateur ne peut pas être suspendu ou supprimé via les écrans membres.

## Liens

Une capacité est l’UUID du lien accompagné d’un HMAC-SHA256 lié à son usage (partage ou invitation), calculé avec `SECRET_KEY`. Connaître la seule base ne permet pas de reconstruire les liens. Connaître la clé et la base le permet : protégez-les ensemble. La rotation de la clé invalide les anciens liens et invitations.

Les fichiers et métadonnées d’un lien avec mot de passe ne sont visibles qu’après déverrouillage. La permission de session expire après une heure. Les statuts et l’activité du propriétaire sont revérifiés au téléchargement.

Le ZIP est créé sur disque, puis une mise à jour conditionnelle atomique réserve une utilisation et écrit l’événement dans la même transaction. Si un fichier manque, aucune utilisation n’est consommée. Si le navigateur abandonne ensuite, l’utilisation reste consommée. Deux demandes concurrentes ne peuvent obtenir une autorisation unique.

La révocation et la réservation ont un ordre atomique en base : si la révocation gagne, le téléchargement est bloqué ; si la réservation a déjà gagné, le transfert peut se poursuivre. L’ajout de fichiers est bloqué tant qu’un lien actif existe. Supprimer un ou plusieurs fichiers révoque tous les liens non révoqués des dossiers concernés.

## Fichiers et disponibilité

Les limites sont appliquées à plusieurs niveaux : proxy, corps de requête, réception par blocs, fichier, dossier, nombre de fichiers et quota utilisateur sous verrou. Les imports non connectés ou sur un autre compte sont refusés avant le parsing multipart.

L’extension est une première règle, pas une preuve du type réel. L’antivirus analyse les octets. Les archives ne sont jamais extraites par l’application. Les téléchargements sont `application/octet-stream` et `attachment` ; aucun contenu utilisateur actif n’est exécuté. La version 0.4 ne fait pas de validation sémantique intégrale de tous les formats.

ClamAV utilise INSTREAM sur un réseau Docker privé. Toute réponse autre que `stream: OK` bloque l’enregistrement. Les limites dépassées et archives chiffrées sont signalées via la configuration du démon. L’application n’enregistre un fichier comme analysé qu’après un résultat accepté.

Les ZIP temporaires sont bornés par la taille d’un dossier (5 Gio par défaut). Chaque requête nécessite une réservation d’espace temporaire avant création, conservée jusqu’à la fermeture du transfert. Un verrou POSIX partagé sérialise les admissions sur le même hôte et tient compte des autres ZIP, imports classiques et réservations reprenables. Les imports classiques contrôlent aussi quota et capacité avant le parsing puis pendant la réception. La limitation de fréquence par IP et les limites du serveur réduisent l’abus sans remplacer une protection DDoS. Il n’y a pas encore de budget global de bande passante ni de file d’attente de génération d’archives.

La suppression SQL et la suppression physique ne forment pas une transaction distribuée. Les blobs sont supprimés après validation SQL ; un échec système peut laisser un blob inaccessible. La maintenance supprime les blobs orphelins vieux de plus de 24 h. Après un crash pendant import, le même mécanisme récupère les fichiers écrits avant un rollback.

## Vie privée et logs

Le journal métier conserve type d’événement et date, pas l’IP, le navigateur ou une identité prétendue du destinataire. Les cookies servent aux sessions, au CSRF, au déverrouillage et à la déduplication des ouvertures. Ils ne constituent pas une mesure fiable d’audience humaine.

Les clés de limitation contiennent un HMAC de l’IP ou de l’adresse de connexion avec fenêtre temporelle ; elles sont purgées. Les historiques sont purgés après 90 jours par défaut. Les compteurs de téléchargements sont conservés pour continuer à appliquer les limites. Les fichiers persistent jusqu’à suppression explicite ; l’expiration d’un lien ne supprime pas sa bibliothèque.

Les logs d’accès Caddy/Gunicorn sont désactivés par défaut. Les logs Django expurgent les URL de partage, invitation et récupération. N’ajoutez pas une journalisation de requêtes, corps, cookies ou variables d’environnement en production. Les liens sortants n’envoient pas de référent ; la politique `same-origin` conserve les contrôles CSRF à l’intérieur de l’application.

## Vérifications

Tests automatisés sur le modèle d’autorisations, mots de passe, invitations, quotas, CSRF, chemins, absence de consommation sur GET/HEAD, révocation, concurrence, suppression et export CSV. Tests du protocole ClamAV avec réponses propres, malveillantes et invalides ; test d’intégration EICAR en CI.

Ces tests ne remplacent ni un audit indépendant, ni une surveillance des versions, ni un test de charge adapté à votre hébergement.


## Protection et fiabilité ajoutées en 0.2

TOTP utilise une clé chiffrée avec Fernet, des étapes temporelles non réutilisables et des codes de récupération hachés à usage unique. La connexion par mot de passe ne crée qu’une session provisoire de cinq minutes. Les changements de MFA révoquent les autres sessions.

Les fichiers supprimés restent privés dans la corbeille et dans le quota. La restauration ne réactive jamais les liens. Les envois par blocs réservent leur quota sous verrou, contrôlent leurs empreintes et publient le lot uniquement après analyse antivirus. Les blocs temporaires expirent après 24 h. Les noms de fichiers de la corbeille restent réservés.

La surveillance n’expose les métriques que sur une route à jeton Bearer ; les alertes e-mail nécessitent une configuration explicite. Les sauvegardes sont restaurées dans une instance isolée pour vérifier les octets, les compteurs et les clés MFA. Voir [RELIABILITY.md](RELIABILITY.md) pour les limites et hypothèses d’exploitation.

La [revue de code préalable à 0.2](SECURITY_REVIEW.md) décrit les corrections et leurs tests.

## Aperçus ajoutés en 0.3

La galerie est optionnelle par lien et désactivée pour les liens créés avant la migration. Chaque dérivé est autorisé avant lecture du cache et après son rendu : mot de passe, appartenance du fichier, état du compte, corbeille, révocation, expiration et nombre de téléchargements. Consulter un aperçu ne consomme pas un téléchargement ; une copie du contenu affiché reste possible.

Les originaux ne sont jamais renvoyés par la route d’aperçu. Pillow et PDFium produisent un JPEG réduit sans métadonnées dans un conteneur sans réseau, sans secret, sans volume de fichiers et avec racine en lecture seule. Le processus de rendu, le cache, les dimensions, les tailles et les durées sont bornés. Le [guide des aperçus](PREVIEWS.md) précise ces limites et le mode local de développement, qui ne possède pas l’isolation Docker. Cette nouvelle surface nécessite le maintien à jour des décodeurs natifs.


## Automatisations ajoutées en 0.4

L’API accepte uniquement un en-tête Bearer explicite, jamais les cookies de session. Chaque clé appartient à un compte, possède des permissions et une expiration, et n’est conservée que sous empreinte HMAC. Les écritures revérifient la clé sous le même verrou utilisateur que sa révocation. Un changement de mot de passe/MFA invalide les clés existantes ; une suspension les révoque définitivement et met les webhooks en pause. Les imports réutilisent les contrôles de fichiers, antivirus et quotas de l’interface. Les reçus d’idempotence sont chiffrés, bornés et supprimés après 24 heures.

Les webhooks transmettent uniquement des identifiants et le type d’événement. URL et secrets de signature sont chiffrés avec la clé de chiffrement de l’instance. Chaque tentative résout et vérifie toutes les adresses DNS, fixe une IP publique pour la connexion TLS au nom d’origine, interdit les redirections et ignore les proxys d’environnement. Le processus dédié dispose de PostgreSQL et d’un accès HTTPS sortant, sans volume de fichiers. Les signatures HMAC, horodatages et identifiants permettent au destinataire de vérifier l’origine et de rejeter les répétitions ; il doit implémenter ces vérifications. Files d’attente, durées, tentatives et rétention sont bornées. Voir la [référence API](API.md) pour le contrat et ses limites.

Les guides affichés dans l’application proviennent uniquement de Markdown versionné dans le dépôt, avec HTML brut désactivé. Les traductions portent sur l’interface, pas sur les contenus des utilisateurs.
