# Politique de sécurité

La branche principale de Liasio 0.4 reçoit les correctifs. Le projet est jeune et n’a pas encore fait l’objet d’un audit humain indépendant.

## Signaler une vulnérabilité

Utilisez le [signalement privé GitHub](https://github.com/fmacfa/liasio/security/advisories/new). N’ouvrez pas une issue publique contenant un secret, un lien réel, un fichier client ou une procédure d’exploitation contre une installation existante.

Incluez la version/commit, les conditions de reproduction sur une installation de test, le résultat attendu, le résultat observé et l’impact potentiel. Ne testez que les systèmes que vous êtes autorisé à examiner.

## Périmètre sensible

Initialisation, invitations, sessions, réinitialisation des mots de passe, isolation des comptes, traversée de chemins, imports, antivirus, quotas, capacités des liens, concurrence des téléchargements, exports et suppression.

Le [modèle de sécurité](docs/SECURITY_MODEL.md) documente les garanties visées et les limites. Avant de publier une instance, suivez le [guide d’exploitation](docs/OPERATIONS.md).

Un lien volontairement transmis à une personne peut être retransmis. La révocation ne supprime pas une copie déjà téléchargée. Le serveur peut lire les fichiers. Ces comportements sont documentés et ne constituent pas un chiffrement de bout en bout.
