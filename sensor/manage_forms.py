from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.utils.translation import gettext_lazy as _

from .models import Container, District, Osi, Reward
from .roles import ROLE_DISTRICT, ROLE_HEAD, ROLE_WORKER

ROLE_CHOICES = [
    (ROLE_HEAD, _("Бас диспетчер — бүкіл қаланы және аудандарды бақылайды")),
    (ROLE_DISTRICT, _("Аудан диспетчері — өз ауданының өтініштерін өңдейді")),
    (ROLE_WORKER, _("Жұмысшы — тапсырмаларды орындайды")),
]

REQUIRED = {"required": _("Бұл өрісті толтырыңыз.")}


class StaffForm(forms.Form):

    first_name = forms.CharField(label=_("Аты"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Тегі"), max_length=150, required=False)
    role = forms.ChoiceField(
        label=_("Рөлі"),
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect,
        error_messages=REQUIRED
    )
    district = forms.ModelChoiceField(
        label=_("Аудан"),
        queryset=District.objects.all(),
        required=False,
        empty_label=_("Таңдалмаған")
    )
    is_active = forms.BooleanField(
        label=_("Белсенді: жүйеге кіре алады"),
        required=False,
        initial=True
    )

    def clean(self):

        cleaned = super().clean()

        if cleaned.get("role") in (ROLE_DISTRICT, ROLE_WORKER) and not cleaned.get("district"):
            self.add_error("district", _("Аудан диспетчері мен жұмысшыға аудан таңдаңыз."))

        # The head dispatcher oversees the whole city, so no district is kept
        if cleaned.get("role") == ROLE_HEAD:
            cleaned["district"] = None

        return cleaned


class PasswordFields(forms.Form):

    password1 = forms.CharField(
        label=_("Құпия сөз"),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        error_messages=REQUIRED
    )
    password2 = forms.CharField(
        label=_("Құпия сөзді қайталаңыз"),
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        error_messages=REQUIRED
    )

    def check_passwords(self, cleaned, user=None):

        first, second = cleaned.get("password1"), cleaned.get("password2")

        if first and second and first != second:
            self.add_error("password2", _("Құпия сөздер сәйкес келмейді."))
            return

        if first:
            try:
                password_validation.validate_password(first, user=user)
            except forms.ValidationError as error:
                self.add_error("password1", error)


class StaffCreateForm(StaffForm, PasswordFields):

    username = forms.CharField(
        label=_("Логин"),
        max_length=150,
        validators=[UnicodeUsernameValidator()],
        help_text=_("Латын әріптері, сандар және @ . + - _ белгілері"),
        error_messages=REQUIRED
    )

    field_order = [
        "username", "first_name", "last_name", "role", "district",
        "password1", "password2", "is_active",
    ]

    def clean_username(self):

        username = self.cleaned_data["username"]

        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(_("Бұл логин бос емес, басқасын таңдаңыз."))

        return username

    def clean(self):

        cleaned = super().clean()

        self.check_passwords(cleaned, user=get_user_model()(
            username=cleaned.get("username", ""),
            first_name=cleaned.get("first_name", ""),
            last_name=cleaned.get("last_name", ""),
        ))

        return cleaned


class PasswordForm(PasswordFields):

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):

        cleaned = super().clean()

        self.check_passwords(cleaned, user=self.user)

        return cleaned


class ContainerForm(forms.ModelForm):

    class Meta:
        model = Container
        fields = ["name", "location", "latitude", "longitude"]
        labels = {
            "name": _("Атауы"),
            "location": _("Мекенжайы"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": _("Мысалы: Container #9")}),
            "location": forms.TextInput(attrs={"placeholder": _("Мысалы: Медеу ауданы, Достық даңғылы 85")}),
            "latitude": forms.HiddenInput,
            "longitude": forms.HiddenInput,
        }
        error_messages = {
            "name": REQUIRED,
            "location": REQUIRED,
        }

    def clean(self):

        cleaned = super().clean()

        if cleaned.get("latitude") is None or cleaned.get("longitude") is None:
            raise forms.ValidationError(_("Картадан контейнердің орнын белгілеңіз."))

        return cleaned


class RewardForm(forms.ModelForm):

    class Meta:
        model = Reward
        fields = ["title", "partner", "description", "cost", "stock", "is_active"]
        labels = {
            "title": _("Сыйлық"),
            "partner": _("Серіктес"),
            "description": _("Шарты"),
            "cost": _("Бағасы (ұпай)"),
            "stock": _("Саны"),
            "is_active": _("Тұрғындарға көрсетілсін"),
        }
        help_texts = {"stock": _("Бос қалдырсаңыз, шексіз")}
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": _("Мысалы: 20% жеңілдік")}),
            "partner": forms.TextInput(attrs={"placeholder": _("Мысалы: «Жасыл» кофеханасы")}),
            "description": forms.TextInput(attrs={"placeholder": _("Мысалы: Кез келген сусынға, 1 ай жарамды")}),
            "cost": forms.NumberInput(attrs={"min": 1}),
            "stock": forms.NumberInput(attrs={"min": 0}),
        }
        error_messages = {"title": REQUIRED, "partner": REQUIRED, "cost": REQUIRED}


class OsiForm(forms.ModelForm):

    class Meta:
        model = Osi
        fields = ["name", "address", "district", "is_active"]
        labels = {
            "name": _("Атауы"),
            "address": _("Мекенжайы"),
            "district": _("Аудан"),
            "is_active": _("Тұрғындар таңдай алады"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": _("Мысалы: ОСИ «Самал-2, 15»")}),
        }
        error_messages = {"name": REQUIRED}
