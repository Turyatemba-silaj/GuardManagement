from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand

from guardmanagementsystem.models import Client, Guard, Supervisor


ROLE_MODEL_PERMISSIONS = {
    "Admin": ["view", "add", "change", "delete"],
    "Supervisor": ["view", "add", "change"],
    "Guard": ["view", "add"],
    "Client": ["view"],
}

ROLE_MODELS = {
    "Admin": [
        "user", "group", "guard", "supervisor", "client", "deployment", "shift",
        "attendance", "incident", "disciplinaryaction", "salary", "advancerequest",
        "asset", "iotdevice", "rfidcard", "auditlog",
    ],
    "Supervisor": [
        "user", "group", "guard", "supervisor", "client", "deployment", "shift",
        "attendance", "incident", "disciplinaryaction", "salary", "advancerequest",
        "asset", "iotdevice", "rfidcard",
    ],
    "Guard": ["incident", "advancerequest", "shift"],
    "Client": ["deployment", "attendance", "incident", "shift"],
}


def safe_username(prefix, value):
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in str(value or "").strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return f"{prefix}_{cleaned}"[:150]


class Command(BaseCommand):
    help = "Create use-case role groups, assign permissions, and create/link Supervisor, Guard, and Client users."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="ChangeMe@123",
            help="Initial password for newly created users.",
        )

    def handle(self, *args, **options):
        password = options["password"]
        groups = self.create_groups()
        self.assign_admin_group(groups["Admin"])
        self.disable_obsolete_officer_users()
        self.create_supervisor_users(groups["Supervisor"], password)
        self.create_guard_users(groups["Guard"], password)
        self.create_client_users(groups["Client"], password)

        self.stdout.write(self.style.SUCCESS("Role groups and profile users are ready."))
        self.stdout.write(f"Initial password for newly created users: {password}")

    def create_groups(self):
        groups = {}
        for role, models in ROLE_MODELS.items():
            group, _ = Group.objects.get_or_create(name=role)
            group.permissions.clear()
            if role == "Supervisor":
                permissions = Permission.objects.filter(
                    content_type__model__in=models,
                    codename__regex=r"^(view|add|change|delete)_",
                ).exclude(
                    content_type__model="auditlog",
                ).exclude(
                    codename__in=["add_client", "add_guard", "add_supervisor"],
                )
            else:
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

    def disable_obsolete_officer_users(self):
        obsolete_groups = Group.objects.filter(name__in=["Operations Officer", "HR Officer", "Finance Officer"])
        for user in User.objects.filter(groups__in=obsolete_groups).distinct():
            user.groups.remove(*obsolete_groups)
            user.is_active = False
            user.save(update_fields=["is_active"])
            self.stdout.write(f"Disabled obsolete officer user: {user.username}")

    def get_or_create_user(self, username, email, full_name, group, password):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email or "",
                "first_name": str(full_name or "").split(" ")[0][:150],
                "last_name": " ".join(str(full_name or "").split(" ")[1:])[:150],
                "is_active": True,
            },
        )

        if created:
            user.set_password(password)

        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.save()

        user.groups.set([group])
        return user, created

    def create_supervisor_users(self, group, password):
        for supervisor in Supervisor.objects.all():
            username = supervisor.user.username if supervisor.user else safe_username("supervisor", supervisor.supervisor_number or supervisor.full_name)
            user, created = self.get_or_create_user(username, supervisor.email, supervisor.full_name, group, password)
            if supervisor.user_id != user.id:
                supervisor.user = user
                supervisor.save(update_fields=["user"])
            self.stdout.write(f"{'Created' if created else 'Linked'} supervisor user: {username}")

    def create_guard_users(self, group, password):
        for guard in Guard.objects.all():
            username = guard.user.username if guard.user else safe_username("guard", guard.guard_number or guard.full_name)
            user, created = self.get_or_create_user(username, guard.email, guard.full_name, group, password)
            if guard.user_id != user.id:
                guard.user = user
                guard.save(update_fields=["user"])
            self.stdout.write(f"{'Created' if created else 'Linked'} guard user: {username}")

    def create_client_users(self, group, password):
        for client in Client.objects.all():
            username = client.user.username if client.user else safe_username("client", client.client_name)
            user, created = self.get_or_create_user(username, client.email, client.client_name, group, password)
            if client.user_id != user.id:
                client.user = user
                client.save(update_fields=["user"])
            self.stdout.write(f"{'Created' if created else 'Linked'} client user: {username}")
