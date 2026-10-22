from django.utils.translation import gettext_lazy as _
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

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
        min_length=12,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_(
            "Facultatif · 12 caractères minimum. Communiquez-le par un autre canal."
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


class UnlockForm(forms.Form):
    password = forms.CharField(
        label=_("Mot de passe du partage"),
        max_length=128,
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "autofocus": True}),
    )


class BrandingForm(forms.ModelForm):
    class Meta:
        model = Branding
        fields = ["name", "tagline", "theme", "footer"]
        labels = {
            "name": _("Nom affiché"),
            "tagline": _("Signature"),
            "theme": _("Palette"),
            "footer": _("Texte en pied de page"),
        }
        widgets = {"footer": forms.Textarea(attrs={"rows": 3})}


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
