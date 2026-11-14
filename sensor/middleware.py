from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_vary_headers


class LanguageMiddleware:
    """Kazakh unless the visitor picked another language in the header.

    Unlike Django's LocaleMiddleware, the browser's language is not used: the site
    opens in Kazakh for everyone, and the choice is remembered in a cookie.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.languages = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):

        language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)

        if language not in self.languages:
            language = settings.LANGUAGE_CODE

        translation.activate(language)
        request.LANGUAGE_CODE = language

        response = self.get_response(request)

        patch_vary_headers(response, ("Cookie",))
        response.headers.setdefault("Content-Language", language)

        return response
