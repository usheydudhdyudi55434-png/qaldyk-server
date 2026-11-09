from django import forms
from django.contrib.auth import get_user_model

from .models import ResidentRequest
from .roles import WORKERS


MAX_PHOTO_BYTES = 5 * 1024 * 1024

# File signatures, so a renamed non-image is rejected
PHOTO_SIGNATURES = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89PNG\r\n\x1a\n",     # PNG
)

REQUIRED = {"required": "Бұл өрісті толтырыңыз."}


def validate_photo(photo):

    if photo.size > MAX_PHOTO_BYTES:
        raise forms.ValidationError("Фото 5 МБ-тан аспауы керек.")

    head = photo.read(12)
    photo.seek(0)

    is_webp = head[:4] == b"RIFF" and head[8:12] == b"WEBP"

    if not (is_webp or head.startswith(PHOTO_SIGNATURES)):
        raise forms.ValidationError("Тек JPG, PNG немесе WEBP суретін жүктеңіз.")

    return photo


class ResidentRequestForm(forms.ModelForm):

    # Hidden from people; bots that fill every field are turned away
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={
        "tabindex": "-1",
        "autocomplete": "off"
    }))

    class Meta:
        model = ResidentRequest
        fields = ["category", "address", "description", "photo", "latitude", "longitude"]
        widgets = {
            "category": forms.RadioSelect,
            "address": forms.TextInput(attrs={
                "placeholder": "Мысалы: Абай даңғылы 12, аула ішінде",
                "autocomplete": "street-address"
            }),
            "description": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Не болғанын қысқаша жазыңыз"
            }),
            "photo": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
            "latitude": forms.HiddenInput,
            "longitude": forms.HiddenInput,
        }
        error_messages = {
            "category": REQUIRED,
            "address": REQUIRED,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Radio buttons need no blank "choose one" entry; nothing is preselected
        self.fields["category"].choices = ResidentRequest.Category.choices

    def clean_website(self):
        if self.cleaned_data["website"]:
            raise forms.ValidationError("Өтінішті жіберу мүмкін болмады.")
        return ""

    def clean_photo(self):

        photo = self.cleaned_data.get("photo")

        return validate_photo(photo) if photo else photo

    def clean(self):

        cleaned = super().clean()

        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")

        if (latitude is None) != (longitude is None):
            raise forms.ValidationError("Картадағы нүкте толық көрсетілмеді, қайта белгілеңіз.")

        if latitude is not None and not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise forms.ValidationError("Картадағы нүкте дұрыс емес, қайта белгілеңіз.")

        return cleaned


class RequestUpdateForm(forms.ModelForm):

    class Meta:
        model = ResidentRequest
        fields = ["status", "assignee", "resolution_note"]
        labels = {
            "status": "Мәртебесі",
            "assignee": "Орындаушы жұмысшы",
            "resolution_note": "Тұрғынға жауап",
        }
        widgets = {
            "resolution_note": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Тұрғын өтініш бетінде көреді"
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only workers carry out requests; dispatchers route them
        workers = get_user_model().objects.filter(
            is_active=True,
            groups__name=WORKERS
        ).order_by("username")

        self.fields["assignee"].queryset = workers
        self.fields["assignee"].empty_label = "Тағайындалмаған"

        if not workers.exists():
            self.fields["assignee"].help_text = (
                "Жұмысшылар жоқ. Әкімші оларды /admin/ бетінде «Жұмысшылар» тобына қосады."
            )

    def clean(self):

        cleaned = super().clean()

        active = (ResidentRequest.Status.ACCEPTED, ResidentRequest.Status.IN_PROGRESS)

        if cleaned.get("status") in active and not cleaned.get("assignee"):
            self.add_error("assignee", "Бұл мәртебе үшін орындаушы жұмысшыны таңдаңыз.")

        return cleaned


class TaskCompleteForm(forms.ModelForm):
    """What a worker sends when the job is done; the photo is the proof."""

    class Meta:
        model = ResidentRequest
        fields = ["result_photo", "resolution_note"]
        labels = {
            "result_photo": "Орындалғаннан кейінгі фото",
            "resolution_note": "Тұрғынға жауап",
        }
        widgets = {
            "result_photo": forms.ClearableFileInput(attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "capture": "environment"
            }),
            "resolution_note": forms.Textarea(attrs={
                "rows": 2,
                "placeholder": "Мысалы: контейнер босатылды, аула тазартылды"
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["result_photo"].required = True
        self.fields["result_photo"].error_messages["required"] = (
            "Жұмыс аяқталғанын көрсететін фото қажет."
        )

    def clean_result_photo(self):
        return validate_photo(self.cleaned_data["result_photo"])
