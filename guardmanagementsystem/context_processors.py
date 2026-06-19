from .role_access import nav_permissions, user_role_names
from .models import Supervisor


def role_access(request):
    user = getattr(request, "user", None)
    if not user:
        return {}

    pending_advance_notifications = 0
    pending_client_notifications = 0
    if user.is_authenticated:
        supervisor = Supervisor.objects.filter(user=user).first()
        if supervisor:
            pending_advance_notifications = supervisor.advance_notifications
            pending_client_notifications = supervisor.client_notifications

    return {
        "nav_permissions": nav_permissions(user),
        "user_roles": sorted(user_role_names(user)),
        "pending_advance_notifications": pending_advance_notifications,
        "pending_client_notifications": pending_client_notifications,
    }
