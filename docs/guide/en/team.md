# Invite and manage members

Only an administrator can open **Members**. Members have separate files, links, branding, API keys and webhooks. Being an administrator does not add other members’ file lists to your library; it exposes operational counts and quotas.

![Members and invitation form](../../screenshots/user-guide/en/team.png)

## Invite a member

1. In **Members**, enter the person’s email under **Invite a member**.
2. Choose **Create invitation**.
3. Copy the new invitation and send it to that person through your chosen channel. Liasio does not automatically email invitations.
4. The recipient opens it, confirms the displayed address and creates a name and password. Invited members are not administrators.

The invitation expires after seven days and can be accepted once. The email address is fixed. You cannot invite an address that already has an account. To cancel an unused invitation, choose **Revoke**; to replace an expired one, create another.

## Suspend or reactivate

Find the member and choose **Suspend**, then confirm. Suspension ends their sessions, revokes their share links and API keys, and pauses their webhooks. Files remain stored and quota remains charged. An in-flight transfer or webhook request may finish.

Choose **Reactivate** to allow them to sign in again. Revoked links and keys stay revoked, and webhooks stay paused. The member must issue new links/keys and explicitly reactivate webhooks as needed. Administrator accounts cannot be suspended through this page.

## Review administration and quotas

The page shows the ten latest administration events. Use **Operations → Member quotas** to adjust storage limits. Permanent account erasure is a server command, documented in [Operations](../../OPERATIONS.md); suspension is the reversible choice for temporarily stopping access.
