"""One checked-in Markdown source for GitHub and in-app documentation."""

from pathlib import PurePosixPath
import posixpath
from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import get_language, gettext_lazy as _
from django.views.decorators.http import require_GET
from markdown_it import MarkdownIt

GUIDES = [
    (
        "getting-started",
        _("Premiers pas"),
        _("Installation, connexion et langue de l’interface."),
    ),
    (
        "collections",
        _("Organiser ses dossiers"),
        _("Créer un dossier et préparer sa présentation."),
    ),
    (
        "uploads",
        _("Importer des fichiers"),
        _("Fichiers, dossiers, progression et reprise des envois."),
    ),
    (
        "sharing",
        _("Partager et prévisualiser"),
        _("Conditions, mots de passe, aperçus et révocation."),
    ),
    (
        "branding",
        _("Personnaliser sa page"),
        _("Nom, signature, couleurs et pied de page."),
    ),
    (
        "bulk-links",
        _("Créer des liens en lot"),
        _("Quantité, import Excel ou CSV, noms et export des URL."),
    ),
    (
        "bulk-links-api",
        _("Automatiser les lots de liens"),
        _("Envoyer des noms, recevoir les liens et exporter par API."),
    ),
    (
        "storage",
        _("Gérer le stockage"),
        _("Tailles, quotas, suppression et restauration."),
    ),
    (
        "team",
        _("Inviter son équipe"),
        _("Invitations, comptes et suspension des accès."),
    ),
    (
        "account",
        _("Protéger son compte"),
        _("Mot de passe, double authentification et récupération."),
    ),
    (
        "activity",
        _("Suivre les accès"),
        _("Ouvertures, téléchargements et export CSV."),
    ),
    (
        "operations",
        _("Administrer l’instance"),
        _("Capacité, antivirus, sauvegardes et maintenance."),
    ),
    (
        "automation",
        _("Automatiser avec l’API et les webhooks"),
        _("Clés, import automatique, événements signés et n8n."),
    ),
]


def context(request):
    return {
        "nav": "help",
        "help_layout": "layout.html"
        if request.user.is_authenticated
        else "help/public.html",
    }


@require_GET
def index(request):
    return render(request, "help/index.html", {**context(request), "guides": GUIDES})


@require_GET
def guide(request, slug):
    item = next((g for g in GUIDES if g[0] == slug), None)
    if item is None:
        raise Http404
    language = (get_language() or settings.LANGUAGE_CODE).split("-")[0]
    if language not in dict(settings.LANGUAGES):
        language = settings.LANGUAGE_CODE
    source = settings.BASE_DIR / "docs" / "guide" / language / f"{slug}.md"
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(source.read_text())
    for block in tokens:
        for token in block.children or []:
            if token.type == "image":
                path = PurePosixPath(token.attrGet("src"))
                if "screenshots" in path.parts:
                    relative = path.parts[path.parts.index("screenshots") + 1 :]
                    token.attrSet("src", static("guide/" + "/".join(relative)))
                    token.attrSet("loading", "lazy")
            elif token.type == "link_open":
                href = token.attrGet("href") or ""
                target = href.split("#")[0]
                fragment = "#" + href.split("#", 1)[1] if "#" in href else ""
                if (
                    target.endswith(".md")
                    and "/" not in target
                    and target != "README.md"
                ):
                    token.attrSet(
                        "href", reverse("guide", args=[target[:-3]]) + fragment
                    )
                elif target == "README.md":
                    token.attrSet("href", reverse("help"))
                elif target.startswith("../../"):
                    token.attrSet(
                        "href",
                        "https://github.com/fmacfa/liasio/blob/main/"
                        + posixpath.normpath(f"docs/guide/{language}/" + target)
                        + fragment,
                    )
    # Only repository-owned Markdown, raw HTML disabled. No user-supplied content.
    html = parser.renderer.render(tokens, parser.options, {})
    rendered = mark_safe(html)  # nosec B308, B703
    return render(
        request,
        "help/guide.html",
        {
            **context(request),
            "guide_title": item[1],
            "guide_html": rendered,
            "source_url": f"https://github.com/fmacfa/liasio/blob/main/docs/guide/{language}/{slug}.md",
        },
    )
