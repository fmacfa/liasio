# Inviter et gérer les membres

Seul un administrateur peut ouvrir **Membres**. Chaque membre possède ses fichiers, liens, personnalisation, clés API et webhooks. Le rôle administrateur affiche les volumes et quotas, sans ajouter les listes de fichiers des autres membres à votre bibliothèque.

![Membres et formulaire d’invitation](../../screenshots/user-guide/fr/team.png)

## Inviter un membre

1. Dans **Membres**, saisissez l’e-mail sous **Inviter un membre**.
2. Cliquez sur **Créer l’invitation**.
3. Copiez le nouveau lien et transmettez-le à la personne par le canal de votre choix. Liasio n’envoie pas automatiquement les invitations par e-mail.
4. Le destinataire ouvre le lien, vérifie l’adresse affichée puis choisit son nom et son mot de passe. Les invités ne sont pas administrateurs.

L’invitation expire après sept jours et s’accepte une seule fois. L’adresse e-mail est fixée. Vous ne pouvez pas inviter une adresse déjà associée à un compte. Pour annuler une invitation inutilisée, cliquez sur **Révoquer** ; pour remplacer une invitation expirée, créez-en une nouvelle.

## Suspendre ou réactiver

Repérez le membre, cliquez sur **Suspendre** puis confirmez. La suspension ferme ses sessions, révoque ses liens et clés API et met ses webhooks en pause. Ses fichiers restent conservés et comptés dans son quota. Un transfert ou webhook déjà en cours peut se terminer.

**Réactiver** autorise une nouvelle connexion. Les liens et clés révoqués le restent, les webhooks restent en pause. Le membre doit créer de nouveaux liens et clés et réactiver explicitement ses webhooks selon ses besoins. Les comptes administrateurs ne peuvent pas être suspendus depuis cette page.

## Consulter le journal et les quotas

La page montre les dix derniers événements d’administration. Dans **Exploitation → Quotas des membres**, ajustez les limites de stockage. L’effacement définitif d’un compte est une commande serveur, documentée dans [Exploitation](../../OPERATIONS.md) ; la suspension permet de couper temporairement l’accès de façon réversible.
