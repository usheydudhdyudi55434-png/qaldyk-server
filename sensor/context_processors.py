from .roles import ROLE_LABELS, is_dispatcher, is_worker, role_of, staff_district


def initials(user):

    parts = [part for part in (user.first_name, user.last_name) if part]

    if len(parts) > 1:
        return (parts[0][0] + parts[1][0]).upper()

    return (parts[0] if parts else user.get_username())[:2].upper()


def staff_scope(request):
    """What every staff page needs for its header: role, district, menu and the new-request count."""

    user = request.user

    if not user.is_authenticated:
        return {"staff_district": None, "is_head_dispatcher": False}

    district = staff_district(user)
    dispatcher = is_dispatcher(user)
    match = getattr(request, "resolver_match", None)

    context = {
        "staff_district": district,
        "is_head_dispatcher": dispatcher and district is None,
        "nav_dispatcher": dispatcher,
        "nav_worker": is_worker(user),
        "nav_current": match.url_name if match else "",
        "nav_role": ROLE_LABELS[role_of(user)],
        "nav_initials": initials(user),
    }

    if dispatcher:
        from .models import ResidentRequest
        from .roles import scope_by_district

        context["nav_new_requests"] = scope_by_district(
            ResidentRequest.objects.filter(status=ResidentRequest.Status.NEW, parent__isnull=True), user
        ).count()

    return context
