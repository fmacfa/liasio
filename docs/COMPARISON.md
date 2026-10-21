# Projets étudiés et décisions de conception

Étude documentaire du 21 octobre 2026, basée sur les README et documentations officiels. Il ne s’agit ni d’un audit de sécurité des projets cités ni d’un test comparatif de performances.

| Projet | Ce qui nous intéresse | Décision pour LinkVault |
| --- | --- | --- |
| [Pingvin Share](https://github.com/stonith404/pingvin-share) | Partage rapide, expiration, mots de passe, limites de visiteurs, partages inversés, ClamAV, stockage local/S3 | Conserver un parcours simple ; distinguer clairement visite et autorisation de téléchargement. Le README de l’original indique son archivage et renvoie vers un fork ; ne pas dépendre de ce code. |
| [ProjectSend](https://github.com/projectsend/projectsend) | Gestion de clients et groupes, journaux, thèmes, quotas, arborescences et téléchargements ZIP | Séparer les comptes et leurs ressources ; donner aux dossiers une présentation utilisable pour des échanges professionnels. |
| [Gokapi](https://github.com/Forceu/Gokapi) | Expiration selon le temps ou les téléchargements, rôles, demandes de fichiers, déduplication, options de chiffrement et S3 | Mettre expiration, quota et consommation au centre du modèle ; prévoir demandes de fichiers et gros volumes dans la suite du projet. |
| [Send](https://github.com/timvisee/send) | Chiffrement de bout en bout et partage éphémère, continuation communautaire de Firefox Send | Documenter franchement le modèle de confiance. Le serveur LinkVault v0.1 lit les fichiers pour analyser et assembler le ZIP ; un mode E2EE demanderait une architecture distincte. |

## Apport visé

LinkVault réunit un dossier conservé dans une bibliothèque, plusieurs liens avec des conditions indépendantes, une page de présentation par compte et un journal qui nomme précisément ce qu’il mesure. Ces choix répondent à notre usage ; leur présence ici n’implique pas leur absence dans les projets cités.

La page **Stockage** complète le parcours : voir les fichiers réels, trier les plus volumineux, mesurer l’espace récupérable et confirmer une suppression qui révoque aussi les accès concernés.

La vérification porte sur les contrats fonctionnels difficiles : autorisations entre comptes, courses sur les quotas, usage unique simultané, révocation avant l’envoi, échec de l’antivirus, arrêt des imports avant lecture pour les visiteurs non connectés.

## Licences et provenance

Le README de ProjectSend indique GPLv2 ou ultérieure, celui de Gokapi AGPL3, et celui de Send MPL 2.0. Nous n’avons repris ni code, ni captures, ni éléments graphiques de ces projets. Les interfaces et l’implémentation de LinkVault sont nouvelles et distribuées sous MIT ; les bibliothèques tierces conservent leur licence.

## Références de sécurité utilisées

- [Django — sécurité](https://docs.djangoproject.com/en/5.2/topics/security/) : échappement, CSRF, hôtes et HTTPS.
- [Django — CSRF et politique de référent](https://docs.djangoproject.com/en/5.2/ref/csrf/#removing-the-referer-header) : préserver les vérifications du même site tout en évitant la fuite des liens vers des sites tiers.
- [OWASP — File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) : stockage privé, tailles, noms et défense en profondeur.
- [OWASP — Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) : hachage adaptatif Argon2id.
- [ClamAV — protocole et analyse](https://docs.clamav.net/manual/Usage/Scanning.html) : analyse en flux et protection du port du démon.
- [ClamAV — Docker](https://docs.clamav.net/manual/Installing/Docker.html) : mémoire, signatures et version d’image.
- [Caddy — limite du corps de requête](https://caddyserver.com/docs/caddyfile/directives/request_body) : plafonnement de l’import au proxy.
