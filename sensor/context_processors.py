from .roles import is_head_dispatcher, staff_district


def staff_scope(request):
    """Lets every template show which district the signed-in staff member manages."""

    return {
        "staff_district": staff_district(request.user),
        "is_head_dispatcher": is_head_dispatcher(request.user),
    }
