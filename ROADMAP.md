# Évolutions proposées

La [version 0.3](CHANGELOG.md) ajoute une galerie d’images et des aperçus de la première page des PDF, avec un moteur isolé. Elle complète TOTP, les codes de récupération, la corbeille, les transferts reprenables, les quotas administrables, la surveillance, la restauration testée et la publication des images Docker de la version 0.2.

## Prochaines priorités

- Passkeys, obligation de MFA par politique d’équipe et écran des sessions actives.
- Audit humain indépendant avant une version 1.0 et exercices de restauration hors du serveur d’origine.
- Sauvegardes chiffrées vers un stockage externe, avec rétention configurable.
- Mesures de charge sur plusieurs tailles de serveurs et tests de réseaux instables.
- Logo et couverture, décodés puis réencodés en images sûres.

## Échanges clients

- Liens de demande de fichiers, temporaires, avec quota dédié et antivirus obligatoire.
- Stockage S3 compatible et service de téléchargement séparé.
- Vérification du destinataire par code e-mail et notifications facultatives de téléchargement.
- Versions immuables des dossiers et remplacement explicite des documents.
- Politique de suppression des dossiers distincte de l’expiration de leurs liens.

## Organisations et intégrations

- Espaces d’équipe, SSO OIDC et rôles plus fins.
- API à jetons de portée limitée et webhooks signés.
- Interface disponible en français et en anglais.
- Étendre les formats de prévisualisation en conservant l’isolation du moteur et l’absence de contenu actif.
- Chiffrement de bout en bout conçu séparément, avec gestion de clés et récupération explicites. Il change les possibilités d’analyse antivirus côté serveur.

## Choix à conserver

Pas d’import anonyme par défaut, pas de consommation d’un lien par une simple ouverture, aucune prétention de preuve de réception, pas de suivi publicitaire et des comportements d’échec documentés.
