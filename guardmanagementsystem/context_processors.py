from .role_access import nav_permissions, user_role_names


def role_access(request):
    user = getattr(request, "user", None)
    if not user:
        return {}

    return {
        "nav_permissions": nav_permissions(user),
        "user_roles": sorted(user_role_names(user)),
    }
