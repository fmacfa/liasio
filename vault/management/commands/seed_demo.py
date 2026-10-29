import os
from datetime import timedelta
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from vault.models import Branding, Collection, Event, Instance, ShareLink, User
from vault.storage import add_files


class Command(BaseCommand):
    help = "Create clearly fictional demo content, only with DEBUG=true."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo seeding is only allowed with DEBUG=true.")
        password = os.environ.get("DEMO_PASSWORD", "")
        if len(password) < 12:
            raise CommandError(
                "Set DEMO_PASSWORD to at least 12 characters in the local environment."
            )
        if User.objects.exists():
            raise CommandError("Demo requires an empty installation.")
        user = User.objects.create_superuser(
            "demo@example.com", password, display_name="Atelier Nova"
        )
        Branding.objects.create(
            user=user,
            name="Atelier Nova",
            tagline="Des idées à partager.",
            footer="Merci pour votre confiance. — L’équipe Atelier Nova",
        )
        Instance.objects.update_or_create(pk=1, defaults={"initialized": True})
        examples = [
            (
                "Présentation du studio",
                "Notre approche, nos idées et quelques mots pour faire connaissance.",
                [
                    (
                        "Bienvenue.txt",
                        "Bienvenue chez Atelier Nova. Ceci est un document de démonstration de Liasio.\n",
                    ),
                    (
                        "Studio/Notre approche.txt",
                        "Écouter. Imaginer. Réaliser.\nDonnées fictives pour la démonstration.\n",
                    ),
                ],
            ),
            (
                "Identité de marque",
                "Les mots et les couleurs qui racontent notre histoire.",
                [
                    (
                        "Palette.txt",
                        "Forêt : #245e48\nSaule : #deecad\nExemple de contenu.\n",
                    ),
                    ("Signature.txt", "Atelier Nova — Des idées à partager.\n"),
                ],
            ),
            (
                "Documents du projet",
                "Les informations utiles pour préparer notre prochain échange.",
                [
                    (
                        "Projet/Prochaines étapes.txt",
                        "1. Rencontre de découverte\n2. Première proposition\n3. Retour sur les pistes\nExemple fictif.\n",
                    )
                ],
            ),
        ]
        for index, (title, description, files) in enumerate(examples):
            collection = Collection.objects.create(
                owner=user,
                title=title,
                description=description,
                recipient_note="Ces documents sont des exemples de démonstration.",
            )
            uploads = [
                SimpleUploadedFile(path.split("/")[-1], content.encode())
                for path, content in files
            ]
            add_files(user, collection.pk, uploads, [path for path, _ in files])
            link = ShareLink.objects.create(
                collection=collection,
                label=["Découverte du studio", "Équipe créative", "Premier échange"][
                    index
                ],
                expires_at=timezone.now() + timedelta(days=7),
                max_downloads=1 if index == 2 else None,
            )
            Event.objects.create(link=link, kind="created")
        self.stdout.write(
            "Fictional demo ready. Account: demo@example.com. Password is the DEMO_PASSWORD you supplied."
        )
