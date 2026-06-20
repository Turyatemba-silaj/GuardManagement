from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand


ROLE_MODEL_PERMISSIONS = {
    "Admin": ["view", "add", "change", "delete"],
}

ROLE_MODELS = {
    "Admin": [
        "user", "group", "guard", "client", "deployment", "shift",
        "incident", "salary",
        "asset", "assetassignmenthistory", "attendance", "contract",
        "deploymentguard", "iotdevice", "rfidcard", "auditlog",
    ],
}


class Command(BaseCommand):
    help = "Create the Admin role group and promote Admin users to full system access."

    def handle(self, *args, **options):
        groups = self.create_groups()
        self.assign_admin_group(groups["Admin"])
        self.promote_admin_users(groups["Admin"])
        self.disable_obsolete_officer_users()

        self.stdout.write(self.style.SUCCESS("Admin role and full-access users are ready."))

    def create_groups(self):
        groups = {}
        for role, models in ROLE_MODELS.items():
            group, _ = Group.objects.get_or_create(name=role)
            group.permissions.clear()
            actions = ROLE_MODEL_PERMISSIONS[role]
            permissions = Permission.objects.filter(
                content_type__model__in=models,
                codename__regex=rf"^({'|'.join(actions)})_",
            )
            group.permissions.add(*permissions)
            groups[role] = group
        return groups

    def assign_admin_group(self, admin_group):
        for user in User.objects.filter(is_superuser=True):
            user.groups.add(admin_group)

    def promote_admin_users(self, admin_group):
        for user in User.objects.filter(groups=admin_group):
            changed_fields = []
            if not user.is_staff:
                user.is_staff = True
                changed_fields.append("is_staff")
            if not user.is_superuser:
                user.is_superuser = True
                changed_fields.append("is_superuser")

            if changed_fields:
                user.save(update_fields=changed_fields)
                self.stdout.write(f"Promoted admin user to full access: {user.username}")

    def disable_obsolete_officer_users(self):
        obsolete_groups = Group.objects.filter(name__in=["Operations Officer", "HR Officer", "Finance Officer", "Guard", "Client"])
        for user in User.objects.filter(groups__in=obsolete_groups).distinct():
            user.groups.remove(*obsolete_groups)
            user.is_active = False
            user.save(update_fields=["is_active"])
            self.stdout.write(f"Disabled obsolete officer user: {user.username}")
