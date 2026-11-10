from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.validators import UnicodeUsernameValidator

from .models import Container, District
from .roles import ROLE_DISTRICT, ROLE_HEAD, ROLE_WORKER

ROLE_CHOICES = [
    (ROLE_HEAD, "Бас диспетчер — бүкіл қаланы және аудандарды бақылайды"),
    (ROLE_DISTRICT, "Аудан диспетчері — өз ауданының өтініштерін өңдейді"),
    (ROLE_WORKER, "Жұмысшы — тапсырмаларды орындайды"),
]

REQUIRED = {"required": "Бұл өрісті толтырыңыз."}


class StaffForm(forms.Form):

    first_name = forms.CharField(label="Аты", max_length=150, required=False)
    last_name = forms.CharField(label="Тегі", max_length=150, required=False)
    role = forms.ChoiceField(
        label="Рөлі",
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect,
        error_messages=REQUIRED
    )
    district = forms.ModelChoiceField(
        label="Аудан",
        queryset=District.objects.all(),
        required=False,
        empty_label="Таңдалмаған"
    )
    is_active = forms.BooleanField(
        label="Белсенді: жүйеге кіре алады",
        required=False,
        initial=True
    )

    def clean(self):

        cleaned = super().clean()

        if cleaned.get("role") in (ROLE_DISTRICT, ROLE_WORKER) and not cleaned.get("district"):
            self.add_error("district", "Аудан диспетчері мен жұмысшыға аудан таңдаңыз.")

        # The head dispatcher oversees the whole city, so no district is kept
        if cleaned.get("role") == ROLE_HEAD:
            cleaned["district"] = None

        return cleaned


class PasswordFields(forms.Form):

    password1 = forms.CharField(
        label="Құпия сөз",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        error_messages=REQUIRED
    )
    password2 = forms.CharField(
        label="Құпия сөзді қайталаңыз",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        error_messages=REQUIRED
    )

    def check_passwords(self, cleaned, user=None):

        first, second = cleaned.get("password1"), cleaned.get("password2")

        if first and second and first != second:
            self.add_error("password2", "Құпия сөздер сәйкес келмейді.")
            return

        if first:
            try:
                password_validation.validate_password(first, user=user)
            except forms.ValidationError as error:
                self.add_error("password1", error)


class StaffCreateForm(StaffForm, PasswordFields):

    username = forms.CharField(
        label="Логин",
        max_length=150,
        validators=[UnicodeUsernameValidator()],
        help_text="Латын әріптері, сандар және @ . + - _ белгілері",
        error_messages=REQUIRED
    )

    field_order = [
        "username", "first_name", "last_name", "role", "district",
        "password1", "password2", "is_active",
    ]

    def clean_username(self):

        username = self.cleaned_data["username"]

        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Бұл логин бос емес, басқасын таңдаңыз.")

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
            "name": "Атауы",
            "location": "Мекенжайы",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Мысалы: Container #9"}),
            "location": forms.TextInput(attrs={"placeholder": "Мысалы: Медеу ауданы, Достық даңғылы 85"}),
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
            raise forms.ValidationError("Картадан контейнердің орнын белгілеңіз.")

        return cleaned
