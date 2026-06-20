from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ClientForm, DeploymentGuardForm, GuardForm, UserCreateForm
from .models import Attendance, AttendanceSwipe, Client, Contract, Deployment, DeploymentGuard, Guard, IoTDevice, RFIDCard, Salary
from .role_access import nav_permissions, user_can_access


class AdminAccessTests(TestCase):
    def setUp(self):
        self.admin_group = Group.objects.create(name="Admin")

    def test_admin_group_user_can_access_all_record_actions(self):
        user = User.objects.create_user(username="portal_admin", password="pass")
        user.groups.add(self.admin_group)

        self.assertTrue(user_can_access(user, "client_list"))
        self.assertTrue(user_can_access(user, "rfid_card_activate"))
        self.assertTrue(user_can_access(user, "asset_lifecycle_action"))
        self.assertTrue(user_can_access(user, "any_future_admin_view"))

        permissions = nav_permissions(user)
        self.assertTrue(permissions["can_dashboard"])
        self.assertTrue(permissions["can_people"])
        self.assertTrue(permissions["can_operations"])

    def test_guard_user_can_only_access_self_service_pages(self):
        guard_group = Group.objects.create(name="Guard")
        user = User.objects.create_user(username="guard_user", password="pass")
        user.groups.add(guard_group)

        self.assertTrue(user_can_access(user, "iot_swipe_attendance"))
        self.assertTrue(user_can_access(user, "salary_list"))
        self.assertTrue(user_can_access(user, "salary_payslip_from_attendance"))
        self.assertFalse(user_can_access(user, "client_list"))
        self.assertFalse(user_can_access(user, "iot_device_list"))

        permissions = nav_permissions(user)
        self.assertFalse(permissions["can_dashboard"])
        self.assertTrue(permissions["can_guard_self_service"])
        self.assertFalse(permissions["can_reports_devices"])

    def test_admin_role_user_create_form_grants_staff_and_superuser(self):
        form = UserCreateForm(data={
            "username": "new_admin",
            "email": "new_admin@example.com",
            "is_active": "on",
            "role": str(self.admin_group.pk),
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
        })

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.groups.filter(name="Admin").exists())

    def test_user_create_form_defaults_to_admin_only(self):
        form = UserCreateForm()
        self.assertEqual(form.fields["role"].initial, self.admin_group)
        self.assertEqual(list(form.fields["role"].queryset), [self.admin_group])

    def test_record_forms_do_not_expose_user_link_field(self):
        self.assertNotIn("user", GuardForm().fields)
        self.assertNotIn("user", ClientForm().fields)




class DeploymentStatusTests(TestCase):
    def setUp(self):
        self.client_record = Client.objects.create(
            client_name="Status Client",
            contact_person="Client Person",
            phone="0711111111",
            email="status@example.com",
            address="Kampala",
        )

    def test_deployment_before_or_on_end_date_is_running(self):
        deployment = Deployment.objects.create(
            client=self.client_record,
            site_location="Main Gate",
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            status="Active",
        )

        self.assertEqual(deployment.status, "Running")

    def test_deployment_past_end_date_is_closed(self):
        deployment = Deployment.objects.create(
            client=self.client_record,
            site_location="Main Gate",
            start_date=timezone.localdate() - timezone.timedelta(days=5),
            end_date=timezone.localdate() - timezone.timedelta(days=1),
            status="Active",
        )

        self.assertEqual(deployment.status, "Closed")

class DeploymentContractLimitTests(TestCase):
    def setUp(self):
        self.client_record = Client.objects.create(
            client_name="Limited Client",
            contact_person="Client Person",
            phone="0711111111",
            email="limited@example.com",
            address="Kampala",
        )
        self.contract = Contract.objects.create(
            client=self.client_record,
            number_of_guards=2,
            charge_per_guard=100000,
            contract_type="Static Guarding",
            location="Main Gate",
            start_date=timezone.localdate(),
            status="Active",
        )
        self.guards = [
            Guard.objects.create(
                full_name=f"Guard {index}",
                date_of_birth="1990-01-01",
                phone=f"070000000{index}",
                email=f"guard{index}@example.com",
                address="Kampala",
                status="Active",
                daily_rate=7000,
            )
            for index in range(1, 4)
        ]
        self.deployment = Deployment.objects.create(
            client=self.client_record,
            contract=self.contract,
            site_location="Main Gate",
            start_date=timezone.localdate(),
            status="Active",
        )
        for guard in self.guards[:2]:
            DeploymentGuard.objects.create(
                deployment=self.deployment,
                guard=guard,
                start_date=timezone.localdate(),
            )

    def test_deployment_guard_form_blocks_guards_above_contract_limit(self):
        form = DeploymentGuardForm(data={
            "deployment": self.deployment.deployment_id,
            "guard": self.guards[2].guard_id,
            "start_date": timezone.localdate().isoformat(),
            "end_date": "",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("you have excedd contacted number of guards", form.non_field_errors())

class IoTAttendanceApiTests(TestCase):
    def setUp(self):
        self.card = RFIDCard.objects.create(
            card_uid="45a1e32a",
            card_number="CARD-001",
            status="Active",
        )
        self.guard = Guard.objects.create(
            rfid_card=self.card,
            full_name="Musa Test",
            date_of_birth="1990-01-01",
            phone="0700000000",
            email="musa@example.com",
            address="Kampala",
            status="Active",
            daily_rate=7000,
        )
        self.client_record = Client.objects.create(
            client_name="Test Client",
            contact_person="Client Person",
            phone="0711111111",
            email="client@example.com",
            address="Kampala",
        )
        self.deployment = Deployment.objects.create(
            client=self.client_record,
            site_location="Main Gate",
            start_date=timezone.localdate(),
            status="Active",
        )
        DeploymentGuard.objects.create(
            deployment=self.deployment,
            guard=self.guard,
            start_date=timezone.localdate(),
        )
        self.device = IoTDevice.objects.create(
            deployment=self.deployment,
            device_code="GATE-TEST-001",
            api_key="test-api-key",
            is_active=True,
        )

    def post_swipe(self, action="auto"):
        return self.client.post(
            reverse("iot_attendance_api"),
            data={
                "device_code": self.device.device_code,
                "api_key": self.device.api_key,
                "card_id": self.card.card_uid,
                "action": action,
            },
            content_type="application/json",
        )


    def test_iot_swipe_rejects_guard_without_deployment(self):
        loose_card = RFIDCard.objects.create(
            card_uid="loose-card",
            card_number="CARD-LOOSE",
            status="Active",
        )
        Guard.objects.create(
            rfid_card=loose_card,
            full_name="Loose Guard",
            date_of_birth="1990-01-01",
            phone="0700000099",
            email="loose@example.com",
            address="Kampala",
            status="Active",
            daily_rate=7000,
        )

        response = self.client.post(
            reverse("iot_attendance_api"),
            data={
                "device_code": self.device.device_code,
                "api_key": self.device.api_key,
                "card_id": loose_card.card_uid,
                "action": "auto",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "Guard is not actively deployed at this site today.")

    def test_iot_swipe_rejects_deployment_above_contract_guard_limit(self):
        contract = Contract.objects.create(
            client=self.client_record,
            number_of_guards=2,
            charge_per_guard=100000,
            contract_type="Static Guarding",
            location="Main Gate",
            start_date=timezone.localdate(),
            status="Active",
        )
        self.deployment.contract = contract
        self.deployment.save(update_fields=["contract"])

        extra_card = RFIDCard.objects.create(
            card_uid="extra-card",
            card_number="CARD-EXTRA",
            status="Active",
        )
        extra_guard = Guard.objects.create(
            rfid_card=extra_card,
            full_name="Extra Guard",
            date_of_birth="1990-01-01",
            phone="0700000088",
            email="extra@example.com",
            address="Kampala",
            status="Active",
            daily_rate=7000,
        )
        DeploymentGuard.objects.create(
            deployment=self.deployment,
            guard=extra_guard,
            start_date=timezone.localdate(),
        )
        contract.number_of_guards = 1
        contract.save(update_fields=["number_of_guards"])

        response = self.client.post(
            reverse("iot_attendance_api"),
            data={
                "device_code": self.device.device_code,
                "api_key": self.device.api_key,
                "card_id": self.card.card_uid,
                "action": "auto",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "you have excedd contacted number of guards")

    def test_twelfth_auto_swipe_checks_out_and_updates_salary(self):
        for swipe_number in range(1, 12):
            response = self.post_swipe()
            self.assertEqual(response.status_code, 200)

            attendance = Attendance.objects.get(
                guard=self.guard,
                attendance_date=timezone.localdate(),
            )
            attendance.refresh_from_db()
            self.assertIsNone(attendance.check_out_time)
            self.assertEqual(AttendanceSwipe.objects.filter(attendance=attendance).count(), swipe_number)

        salary = Salary.objects.get(guard=self.guard, month=timezone.localdate().strftime("%B"), year=timezone.localdate().year)
        self.assertEqual(salary.attendance_days, 0)

        twelfth_response = self.post_swipe()
        self.assertEqual(twelfth_response.status_code, 200)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, "Running")

        attendance.refresh_from_db()
        self.assertIsNotNone(attendance.check_out_time)
        self.assertEqual(AttendanceSwipe.objects.filter(attendance=attendance).count(), 12)

        salary.refresh_from_db()
        self.assertEqual(salary.attendance_days, 1)

        thirteenth_response = self.post_swipe()
        self.assertEqual(thirteenth_response.status_code, 400)
        self.assertEqual(AttendanceSwipe.objects.filter(attendance=attendance).count(), 12)
