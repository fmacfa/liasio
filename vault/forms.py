from django.utils.translation import gettext_lazy as _
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from datetime import timedelta

from .models import Branding, Collection, ShareLink, User


class AccountForm(forms.Form):
    display_name = forms.CharField(
        label=_("Votre nom"),
        max_length=80,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    password = forms.CharField(
        label=_("Mot de passe"),
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_("Au moins 12 caractères. Préférez une longue phrase unique."),
    )
    password_confirm = forms.CharField(
        label=_("Confirmer le mot de passe"),
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, email="", **kwargs):
        self.email = email
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_confirm"):
            self.add_error(
                "password_confirm", _("Les mots de passe ne correspondent pas.")
            )
        if data.get("password"):
            password_validation.validate_password(
                data["password"],
                User(
                    email=self.email or data.get("email", ""),
                    display_name=data.get("display_name", ""),
                ),
            )
        return data


class SetupForm(AccountForm):
    email = forms.EmailField(
        label=_("Adresse e-mail"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "username"}),
    )
    field_order = ["email", "password", "display_name", "password_confirm"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].widget.attrs["minlength"] = 12


class LoginForm(forms.Form):
    email = forms.EmailField(
        label=_("Adresse e-mail"),
        max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}),
    )
    password = forms.CharField(
        label=_("Mot de passe"),
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ["title", "description", "recipient_note"]
        labels = {
            "title": _("Titre du dossier"),
            "description": _("Présentation"),
            "recipient_note": _("Message pour le destinataire"),
        }
        widgets = {
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": _(
                        "Présentez ce que vous partagez, donnez du contexte…"
                    ),
                }
            )
        }


class LinkForm(forms.ModelForm):
    expiry_mode = forms.ChoiceField(
        label=_("Expiration"),
        required=False,
        initial="none",
        choices=[
            ("none", _("Sans expiration")),
            ("date", _("À une date précise")),
            ("days", _("Dans un nombre de jours")),
        ],
    )
    expires_in_days = forms.IntegerField(
        label=_("Nombre de jours"),
        required=False,
        min_value=1,
        max_value=3650,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*"}),
        help_text=_("À compter de la création du lien, de 1 à 3 650 jours."),
    )
    field_order = [
        "label",
        "expiry_mode",
        "expires_at",
        "expires_in_days",
        "max_downloads",
        "password",
        "allow_previews",
    ]
    allow_previews = forms.BooleanField(
        label=_("Afficher les aperçus des images et PDF"),
        required=False,
        initial=True,
        help_text=_(
            "Images réduites et première page des PDF jusqu’à 20 Mio, visibles avant téléchargement. Leur consultation ne consomme pas le lien et leur copie reste possible."
        ),
    )
    password = forms.CharField(
        label=_("Mot de passe du lien"),
        required=False,
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_(
            "Facultatif, sans longueur minimale. Un mot de passe long reste plus difficile à deviner. Communiquez-le par un autre canal."
        ),
    )

    class Meta:
        model = ShareLink
        fields = ["label", "expires_at", "max_downloads", "allow_previews"]
        labels = {
            "label": _("Nom du lien (visible par vous)"),
            "expires_at": _("Date d’expiration"),
            "max_downloads": _("Nombre maximal de téléchargements"),
        }
        help_texts = {
            "max_downloads": _(
                "Saisissez 1 pour un usage unique. Laissez vide pour un nombre illimité."
            ),
            "expires_at": _(
                "Facultatif · heure Europe/Berlin par défaut (configurable sur le serveur)."
            ),
        }
        widgets = {
            "max_downloads": forms.TextInput(
                attrs={"inputmode": "numeric", "pattern": "[0-9]*"}
            ),
            "expires_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "label": forms.TextInput(attrs={"placeholder": _("Ex. : Envoi à Camille")}),
        }

    def clean_expires_at(self):
        value = self.cleaned_data["expires_at"]
        if value and value <= timezone.now():
            raise ValidationError(_("Choisissez une date future."))
        return value

    def clean(self):
        data = super().clean()
        mode = data.get("expiry_mode")
        if not mode:  # Existing API clients supply only expires_at.
            if data.get("expires_at") and data.get("expires_in_days"):
                raise ValidationError(
                    _("Choisissez une date ou une durée, pas les deux.")
                )
            mode = (
                "days"
                if data.get("expires_in_days") is not None
                else "date"
                if data.get("expires_at")
                else "none"
            )
        if mode == "days":
            days = data.get("expires_in_days")
            if days is None:
                self.add_error("expires_in_days", _("Indiquez le nombre de jours."))
            else:
                data["expires_at"] = timezone.now() + timedelta(days=days)
        elif mode == "date":
            if not data.get("expires_at"):
                self.add_error("expires_at", _("Indiquez une date d’expiration."))
        else:
            data["expires_at"] = None
        return data


class UnlockForm(forms.Form):
    password = forms.CharField(
        label=_("Mot de passe du partage"),
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "autofocus": True}),
    )


class BrandingForm(forms.ModelForm):
    logo = forms.FileField(
        label=_("Logo"),
        required=False,
        widget=forms.FileInput(
            attrs={
                "accept": "image/png,image/jpeg,image/webp",
                "data-brand-image": "logo",
            }
        ),
        help_text=_(
            "PNG, JPEG ou WebP · 10 Mio maximum. Le logo est optimisé automatiquement."
        ),
    )
    remove_logo = forms.BooleanField(label=_("Supprimer le logo"), required=False)
    background = forms.FileField(
        label=_("Image de fond"),
        required=False,
        widget=forms.FileInput(
            attrs={
                "accept": "image/png,image/jpeg,image/webp",
                "data-brand-image": "background",
            }
        ),
        help_text=_(
            "PNG, JPEG ou WebP · 10 Mio maximum. Le fond est optimisé automatiquement."
        ),
    )
    remove_background = forms.BooleanField(
        label=_("Supprimer l’image de fond"), required=False
    )
    field_order = [
        "name",
        "tagline",
        "logo",
        "remove_logo",
        "background",
        "remove_background",
        "theme",
        "accent_color",
        "background_color",
        "footer",
    ]

    def clean(self):
        from .brand_media import clean_image

        data = super().clean()
        for kind in ("logo", "background"):
            if data.get(kind) and data.get(f"remove_{kind}"):
                self.add_error(
                    kind, _("Choisissez une nouvelle image ou sa suppression.")
                )
            elif data.get(kind):
                try:
                    data[kind] = clean_image(data[kind], kind)
                except ValidationError as exc:
                    self.add_error(kind, exc)
        return data

    def save(self, commit=True):
        item = super().save(commit=False)
        for kind in ("logo", "background"):
            if self.cleaned_data.get(f"remove_{kind}"):
                setattr(item, f"{kind}_data", b"")
            elif self.cleaned_data.get(kind):
                setattr(item, f"{kind}_data", self.cleaned_data[kind])
        if commit:
            item.save()
        return item

    class Meta:
        model = Branding
        fields = [
            "name",
            "tagline",
            "theme",
            "accent_color",
            "background_color",
            "footer",
        ]
        labels = {
            "name": _("Nom affiché"),
            "tagline": _("Signature"),
            "theme": _("Palette"),
            "footer": _("Texte en pied de page"),
            "accent_color": _("Couleur principale"),
            "background_color": _("Couleur de fond"),
        }
        help_texts = {
            "accent_color": _(
                "Code hexadécimal, par exemple #245e48. Vide : couleur de la palette."
            ),
            "background_color": _(
                "Code hexadécimal, par exemple #f6f8f1. Vide : fond par défaut."
            ),
        }
        widgets = {
            "footer": forms.Textarea(attrs={"rows": 3}),
            "accent_color": forms.TextInput(
                attrs={"placeholder": "#245e48", "pattern": "#[0-9a-fA-F]{6}"}
            ),
            "background_color": forms.TextInput(
                attrs={"placeholder": "#f6f8f1", "pattern": "#[0-9a-fA-F]{6}"}
            ),
        }


class InvitationForm(forms.Form):
    email = forms.EmailField(label=_("Adresse e-mail du membre"), max_length=254)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("Cette adresse possède déjà un compte."))
        return email


class CurrentPasswordSaveMixin:
    """Never persist unrelated fields from a request's cached account snapshot."""

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)
        with transaction.atomic():
            current = User.objects.select_for_update().get(pk=self.user.pk)
            if (
                not current.is_active
                or not constant_time_compare(current.password, self.user.password)
                or current.mfa_version != self.user.mfa_version
                or current.last_login != self.user.last_login
                or current.email != self.user.email
            ):
                raise ValidationError(
                    _(
                        "Votre compte a changé pendant cette opération. Recommencez la connexion ou la récupération."
                    )
                )
            # The old password / reset authority was validated against this exact
            # credential snapshot; the lock now prevents a concurrent mutation.
            current.set_password(self.cleaned_data["new_password1"])
            current.save(update_fields=["password"])
            self.user = current
            return current


class BoundedPasswordChangeForm(CurrentPasswordSaveMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.max_length = 128
            field.widget.attrs["maxlength"] = 128
            field.validators.append(MaxLengthValidator(128))


class BoundedSetPasswordForm(CurrentPasswordSaveMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.core.validators import MaxLengthValidator

        for field in self.fields.values():
            field.max_length = 128
            field.widget.attrs["maxlength"] = 128
            field.validators.append(MaxLengthValidator(128))
