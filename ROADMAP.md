# Évolutions proposées

Cette liste présente des pistes ; seules les fonctions décrites comme disponibles dans le README font partie de la version 0.1.

## Priorité 1 — renforcer et faciliter l’exploitation

- Double authentification TOTP puis passkeys, codes de récupération et écran des sessions actives.
- Audit indépendant de sécurité et tests de charge documentés avant une version 1.0.
- Quotas modifiables dans l’interface d’administration, alertes de saturation et espace réservé minimal.
- Logo et image de couverture, décodés puis réencodés en images sûres ; styles de présentation supplémentaires.
- Image Docker publiée et signée, nomenclature de versions stable et rapports de dépendances automatisés.
- Procédure de restauration testée automatiquement et sauvegardes chiffrées vers un stockage externe.

## Priorité 2 — échanges clients plus complets

- **Demande de fichiers** : lien de dépôt temporaire, quota dédié, antivirus obligatoire, sans accès aux autres fichiers.
- Téléversements reprenables et découpés en blocs pour les fichiers de plusieurs gigaoctets.
- Stockage S3 compatible, séparation du service de téléchargement et du serveur applicatif.
- Vérification du destinataire par code e-mail à durée limitée, avec affichage clair du niveau d’identité réellement vérifié.
- Notifications facultatives au premier téléchargement et avant expiration ; pas d’e-mails imposés à chaque accès.
- Versions de dossiers immuables et remplacement explicite de documents, avec historique des changements.
- Corbeille avec délai configurable, distincte de la suppression définitive et de la révocation immédiate des liens.
- Politique de suppression automatique des dossiers après une date, séparée de l’expiration d’un lien. Plusieurs liens peuvent partager les mêmes fichiers.

## Priorité 3 — organisations et intégrations

- Espaces d’équipe avec droits explicites, au-delà des bibliothèques personnelles actuelles.
- SSO OIDC, rôles plus fins et politiques centralisées d’expiration et de mot de passe.
- API avec jetons à portée limitée et webhooks signés.
- Internationalisation FR/EN et personnalisation des textes de confidentialité/contact.
- Aperçus de fichiers générés dans un service isolé ; jamais de contenu actif directement sur le domaine de l’application.
- Mode de **chiffrement de bout en bout** conçu séparément : gestion de clés côté navigateur, récupération explicitée et limites assumées. Un serveur qui ne peut pas lire le contenu ne peut pas l’analyser avec ClamAV de la même manière.

## Choix à conserver

- Aucun accès anonyme aux imports par défaut.
- Une ouverture de page ne consomme jamais un lien à téléchargement unique.
- Ne pas présenter un transfert autorisé comme une preuve de réception.
- Aucun suivi publicitaire ni requête tierce sur les pages de partage.
- Des défauts protecteurs et un comportement documenté quand un service échoue.
