"""Reusable non-secret link policies; a password is always supplied per creation."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .access import lock_accounts
from .api_auth import api
from .models import SharingPreset


def values(preset):
    return {
        "expires_in_days": preset.expires_in_days,
        "max_downloads": preset.max_downloads,
        "allow_previews": preset.allow_previews,
    }


def apply_preset(actor, data):
    data = dict(data)
    identifier = data.pop("preset_id", None)
    if not identifier:
        return data
    preset = get_object_or_404(SharingPreset, pk=identifier, owner=actor)
    defaults = values(preset)
    if "expires_at" in data:
        defaults.pop("expires_in_days")
    defaults.update(data)
    if preset.password_required and not defaults.get("password"):
        raise ValidationError(
            _("Ce modèle exige un mot de passe pour chaque nouveau lien.")
        )
    return defaults


class PresetForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data["name"]
        if (
            SharingPreset.objects.filter(owner=self.instance.owner, name=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError(_("Ce nom existe déjà."))
        return name

    class Meta:
        model = SharingPreset
        fields = [
            "name",
            "expires_in_days",
            "max_downloads",
            "password_required",
            "allow_previews",
        ]
        labels = {
            "name": _("Nom du modèle"),
            "expires_in_days": _("Nombre de jours"),
            "max_downloads": _("Nombre maximal de téléchargements"),
            "password_required": _("Exiger un mot de passe à la création"),
            "allow_previews": _("Afficher les aperçus des images et PDF"),
        }
        widgets = {
            key: forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"})
            for key in ("expires_in_days", "max_downloads")
        }


@login_required
@require_http_methods(["GET", "POST"])
def index(request, pk=None):
    item = (
        get_object_or_404(SharingPreset, pk=pk, owner=request.user)
        if pk
        else SharingPreset(owner=request.user)
    )
    form = PresetForm(request.POST if request.method == "POST" else None, instance=item)
    if request.method == "POST":
        with transaction.atomic():
            lock_accounts(request.user, request.user.pk)
            if request.POST.get("action") == "delete" and pk:
                item.delete()
                return redirect("presets")
            if form.is_valid():
                if (
                    not pk
                    and SharingPreset.objects.filter(owner=request.user).count() >= 100
                ):
                    form.add_error(
                        None, _("La limite de 100 modèles par compte est atteinte.")
                    )
                else:
                    form.save()
                    messages.success(request, _("Modèle de partage enregistré."))
                    return redirect("presets")
    return render(
        request,
        "presets.html",
        {
            "nav": "presets",
            "form": form,
            "presets": SharingPreset.objects.filter(owner=request.user),
            "editing": bool(pk),
        },
    )


@api("read")
def api_index(request):
    return JsonResponse(
        {
            "results": [
                {
                    "id": str(p.pk),
                    "name": p.name,
                    "password_required": p.password_required,
                    **values(p),
                }
                for p in SharingPreset.objects.filter(owner=request.user)
            ]
        }
    )
