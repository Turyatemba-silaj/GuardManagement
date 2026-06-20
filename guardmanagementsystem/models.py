from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from datetime import date, datetime
from calendar import monthrange
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
import threading
import re
import secrets


_payroll_signal_state = threading.local()


@contextmanager
def skip_payroll_updates_for_guard_delete(guard_id):
    guard_ids = getattr(_payroll_signal_state, "deleting_guard_ids", set())
    guard_ids.add(guard_id)
    _payroll_signal_state.deleting_guard_ids = guard_ids

    try:
        yield
    finally:
        guard_ids.discard(guard_id)


class AuditLog(models.Model):
    action_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    action = models.CharField(max_length=100)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        username = self.user.username if self.user else "System"
        return f"{username} - {self.action} - {self.created_at}"


def current_year():
    return date.today().year


def next_contract_number():
    year = date.today().year
    prefix = f"CON-{year}-"
    numbers = []

    for value in Contract.objects.filter(contract_number__startswith=prefix).values_list("contract_number", flat=True):
        match = re.match(rf"^{prefix}(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))

    next_number = max(numbers, default=0) + 1
    return f"{prefix}{next_number:04d}"


def next_guard_number():
    numbers = []

    for value in Guard.objects.exclude(guard_number__isnull=True).exclude(guard_number="").values_list("guard_number", flat=True):
        match = re.match(r"GUARD(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))

    next_number = max(numbers, default=0) + 1
    return f"GUARD{next_number:03d}"


class RFIDCard(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Lost', 'Lost'),
        ('Damaged', 'Damaged'),
        ('Returned', 'Returned'),
    ]

    rfid_card_id = models.AutoField(primary_key=True)
    card_uid = models.CharField(max_length=100, unique=True)
    card_number = models.CharField(max_length=100, unique=True)
    issue_date = models.DateField(default=date.today)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')

    def __str__(self):
        return f"{self.card_number} ({self.card_uid})"


class Guard(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Suspended', 'Suspended'),
        ('Terminated', 'Terminated'),
        ('Dismissed', 'Dismissed'),
        ('Resigned', 'Resigned'),
    ]

    guard_id = models.AutoField(primary_key=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='guard_profile')
    guard_number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    rfid_card = models.OneToOneField(RFIDCard, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_guard')
    rfid_card_number = models.CharField(max_length=50, unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    phone = models.CharField(max_length=20)
    email = models.EmailField(unique=True)
    address = models.TextField()
    date_of_joining = models.DateField(auto_now= True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')
    daily_rate = models.DecimalField(max_digits=12, decimal_places=2, default=7000)

    def __str__(self):
        return self.full_name

    @property
    def employee_number(self):
        return self.guard_number


class IoTDevice(models.Model):
    DEVICE_STATUS_CHOICES = [
        (True, "Active"),
        (False, "Inactive"),
    ]

    device_id = models.AutoField(primary_key=True)
    client = models.ForeignKey('Client', on_delete=models.SET_NULL, null=True, blank=True, related_name='iot_devices')
    deployment = models.ForeignKey('Deployment', on_delete=models.SET_NULL, null=True, blank=True, related_name='iot_devices')
    device_name = models.CharField(max_length=100, blank=True)
    device_code = models.CharField(max_length=50, unique=True, blank=True)
    site_location = models.CharField(max_length=200)
    api_key = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(choices=DEVICE_STATUS_CHOICES, default=True)

    def build_device_code(self):
        client = self.client
        site_location = self.site_location
        client_part = f"{client.client_id:03d}" if client else "000"
        site_part = re.sub(r"[^A-Z0-9]+", "-", str(site_location or "SITE").upper()).strip("-") or "SITE"
        prefix = f"GATE-{client_part}-{site_part}"
        next_number = IoTDevice.objects.filter(device_code__startswith=prefix).count() + 1
        device_code = f"{prefix}-{next_number:03d}"

        while IoTDevice.objects.filter(device_code=device_code).exclude(pk=self.pk).exists():
            next_number += 1
            device_code = f"{prefix}-{next_number:03d}"

        return device_code

    def save(self, *args, **kwargs):
        if self.deployment:
            self.client = self.deployment.client
            self.site_location = self.deployment.site_location
        elif self.client and not self.site_location:
            deployment = self.client.deployments.exclude(site_location="").order_by("-start_date").first()
            if deployment:
                self.deployment = deployment
                self.site_location = deployment.site_location

        if not self.device_name:
            client_name = self.client.client_name if self.client else "Client"
            self.device_name = f"{client_name} - {self.site_location} RFID Reader"

        if not self.device_code:
            self.device_code = self.build_device_code()

        if not self.api_key:
            self.api_key = secrets.token_urlsafe(24)

        super().save(*args, **kwargs)

        if self.status == 'Expired' and self.iot_device_id:
            IoTDevice.objects.filter(pk=self.iot_device_id, is_active=True).update(is_active=False)

    def __str__(self):
        return f"{self.device_name} - {self.site_location}"
    
class Client(models.Model):
    client_id = models.AutoField(primary_key=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='client_profile')
    client_name = models.CharField(max_length=100)
    contact_person = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    address = models.TextField()

    def __str__(self):
        return self.client_name

class Contract(models.Model):
    CONTRACT_TYPE_CHOICES = [
        ('Event Security', 'Event Security'),
        ('Static Guarding', 'Static Guarding'),
        ('Mobile Patrol', 'Mobile Patrol'),
        ('Other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('Draft', 'Draft'),
        ('Active', 'Active'),
        ('Expired', 'Expired'),
        ('Terminated', 'Terminated'),
    ]

    contract_id = models.AutoField(primary_key=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='contracts')
    contract_number = models.CharField(max_length=50, unique=True, blank=True)
    number_of_guards = models.PositiveIntegerField()
    day_shift_guards = models.PositiveIntegerField(default=0)
    night_shift_guards = models.PositiveIntegerField(default=0)
    charge_per_guard = models.DecimalField(max_digits=12, decimal_places=2)
    contract_type = models.CharField(max_length=100, choices=CONTRACT_TYPE_CHOICES, default='Static Guarding')
    location = models.CharField(max_length=200, blank=True)
    iot_device = models.ForeignKey(IoTDevice, on_delete=models.SET_NULL, null=True, blank=True, related_name='contracts')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Draft')
    terms = models.TextField(blank=True)

    @property
    def monthly_value(self):
        value = Decimal(self.number_of_guards or 0) * Decimal(self.charge_per_guard or 0)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def shift_distribution_total(self):
        return (self.day_shift_guards or 0) + (self.night_shift_guards or 0)

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'Contract end date cannot be before the start date.'})

        if self.iot_device and self.client_id and self.iot_device.client_id and self.iot_device.client_id != self.client_id:
            raise ValidationError({'iot_device': 'Selected IoT device belongs to another client.'})

        if self.number_of_guards and self.shift_distribution_total != self.number_of_guards:
            raise ValidationError({
                'day_shift_guards': 'Day and night shift guards must add up to the total required guards.',
                'night_shift_guards': 'Day and night shift guards must add up to the total required guards.',
            })

    def sync_expiry_status(self, today=None):
        today = today or date.today()
        if self.status == 'Terminated':
            return
        if self.end_date and self.end_date < today:
            self.status = 'Expired'

    def save(self, *args, **kwargs):
        old_status = self.status
        if self.iot_device:
            if not self.location:
                self.location = self.iot_device.site_location
            if not self.client_id and self.iot_device.client_id:
                self.client = self.iot_device.client

        if not self.contract_number:
            self.contract_number = next_contract_number()

        self.sync_expiry_status()
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and self.status != old_status:
            kwargs['update_fields'] = set(update_fields) | {'status'}

        super().save(*args, **kwargs)

        if self.status == 'Expired' and self.iot_device_id:
            IoTDevice.objects.filter(pk=self.iot_device_id, is_active=True).update(is_active=False)

    def __str__(self):
        return f"{self.contract_number} - {self.client.client_name}"
    
class Deployment(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('On Deployment', 'On Deployment'),
        ('Available', 'Available'),
        ('Has No Deployment', 'Has No Deployment'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
        ('Running', 'Running'),
        ('Closed', 'Closed'),
    ]

    deployment_id = models.AutoField(primary_key=True)
    client = models.ForeignKey(Client,on_delete=models.CASCADE,related_name='deployments')
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True, related_name='deployments')
    shift = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True, related_name='deployments')
    site_location = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'Deployment end date cannot be before the start date.'})

        if self.contract and self.client_id and self.contract.client_id != self.client_id:
            raise ValidationError({'contract': 'Selected contract belongs to another client.'})

        if self.contract_id:
            duplicate = self.__class__.objects.filter(contract_id=self.contract_id)
            if self.pk:
                duplicate = duplicate.exclude(pk=self.pk)
            if duplicate.exists():
                raise ValidationError({'contract': 'This contract is already linked to another deployment.'})


    def sync_date_status(self, today=None):
        today = today or date.today()
        if self.status in ["Cancelled", "Completed"]:
            return
        if self.end_date and self.end_date < today:
            self.status = "Closed"
        else:
            self.status = "Running"

    def save(self, *args, **kwargs):
        if self.contract:
            self.client = self.contract.client
            if not self.site_location:
                self.site_location = self.contract.location
            if not self.start_date:
                self.start_date = self.contract.start_date
            if not self.end_date:
                self.end_date = self.contract.end_date

        self.sync_date_status()
        super().save(*args, **kwargs)

        if self.status == 'Expired' and self.iot_device_id:
            IoTDevice.objects.filter(pk=self.iot_device_id, is_active=True).update(is_active=False)

    def __str__(self):
        return f"{self.client.client_name} - {self.site_location}"


class DeploymentGuard(models.Model):
    STATUS_CHOICES = [
        ('Available for Deployment', 'Available for Deployment'),
        ('On Site', 'On Site'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
    ]

    deployment_guard_id = models.AutoField(primary_key=True)
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name='deployment_guards')
    guard = models.ForeignKey(Guard, on_delete=models.CASCADE, related_name='deployment_guard_assignments')
    deployment_date = models.DateField(default=date.today)
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='Available for Deployment')

    class Meta:
        unique_together = ('deployment', 'guard', 'deployment_date')

    def clean(self):
        if self.check_in_time and self.check_out_time and self.check_out_time < self.check_in_time:
            raise ValidationError("Guard deployment check out cannot be before check in.")

        if self.deployment_id and self.deployment_date:
            if self.deployment_date < self.deployment.start_date:
                raise ValidationError("Guard deployment date cannot be before the deployment start date.")

            if self.deployment.end_date and self.deployment_date > self.deployment.end_date:
                raise ValidationError("Guard deployment date cannot be after the deployment end date.")

        if self.deployment_id and self.deployment.contract_id and self.deployment_date:
            active_statuses = ["Active", "Running", "On Deployment", "Available", "Has No Deployment"]
            existing_assignments = DeploymentGuard.objects.filter(
                deployment__contract=self.deployment.contract,
                deployment__status__in=active_statuses,
                deployment_date=self.deployment_date,
            )

            if self.pk:
                existing_assignments = existing_assignments.exclude(pk=self.pk)

            assigned_guard_count = existing_assignments.values("guard_id").distinct().count()
            if self.guard_id and not existing_assignments.filter(guard_id=self.guard_id).exists():
                assigned_guard_count += 1

            if assigned_guard_count > self.deployment.contract.number_of_guards:
                raise ValidationError("you have excedd contacted number of guards")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

        if self.status == 'Expired' and self.iot_device_id:
            IoTDevice.objects.filter(pk=self.iot_device_id, is_active=True).update(is_active=False)

    def __str__(self):
        return f"{self.guard.full_name} assigned to {self.deployment}"

class Asset(models.Model):
    STATUS_CHOICES = [
        ('Assigned', 'Assigned'),
        ('Available', 'Available'),
        ('Damaged', 'Damaged'),
        ('Lost', 'Lost'),
        ('Returned', 'Returned'),
    ]

    asset_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(Guard,on_delete=models.SET_NULL,
 null=True,
        blank=True,
        related_name='assets'
    )
    asset_name = models.CharField(max_length=100)
    asset_type = models.CharField(max_length=100)
    serial_number = models.CharField(max_length=100, unique=True)
    purchase_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Available')

    def save(self, *args, **kwargs):
        if self.guard_id and self.status in ["Available", "Returned"]:
            self.status = "Assigned"

        if not self.guard_id and self.status == "Assigned":
            self.status = "Available"

        super().save(*args, **kwargs)

        if self.status == 'Expired' and self.iot_device_id:
            IoTDevice.objects.filter(pk=self.iot_device_id, is_active=True).update(is_active=False)

    def __str__(self):
        return f"{self.asset_name} - {self.serial_number}"


class AssetAssignmentHistory(models.Model):
    history_id = models.AutoField(primary_key=True)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="assignment_history")
    guard = models.ForeignKey(Guard, on_delete=models.SET_NULL, null=True, blank=True, related_name="asset_assignment_history")
    assigned_date = models.DateField(default=date.today)
    returned_date = models.DateField(null=True, blank=True)
    condition_on_return = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-assigned_date", "-history_id"]

    def __str__(self):
        guard_name = self.guard.full_name if self.guard else "Unassigned guard"
        return f"{self.asset} - {guard_name}"
    
class Attendance(models.Model):
    STATUS_CHOICES = [
        ('Present', 'Present'),
        ('Absent', 'Absent'),
        ('Late', 'Late'),
        ('On Leave', 'On Leave'),
    ]

    attendance_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(Guard, on_delete=models.CASCADE, related_name='attendances' )
    attendance_date = models.DateField()
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Present')
    replacement_guard = models.ForeignKey( Guard,on_delete=models.SET_NULL, null=True,blank=True, related_name='replacement_attendances' )
    absence_reason = models.TextField(blank=True)

    def __str__(self):
        return f"{self.guard.full_name} - {self.attendance_date}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['guard', 'attendance_date'],
                name='unique_guard_attendance_date'
            )
        ]


class AttendanceSwipe(models.Model):
    SWIPE_TYPE_CHOICES = [
        ('check_in', 'Check In'),
        ('hourly', 'Hourly Swipe'),
        ('check_out', 'Check Out'),
        ('replacement', 'Replacement'),
    ]

    swipe_id = models.AutoField(primary_key=True)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name='swipes')
    iot_device = models.ForeignKey(IoTDevice, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_swipes')
    swipe_time = models.DateTimeField()
    swipe_type = models.CharField(max_length=20, choices=SWIPE_TYPE_CHOICES, default='hourly')

    class Meta:
        ordering = ['swipe_time', 'swipe_id']

    def __str__(self):
        return f"{self.attendance.guard.full_name} - {self.swipe_type} - {self.swipe_time:%Y-%m-%d %H:%M}"

class Shift(models.Model):
    SHIFT_TYPE_CHOICES = [
        ('D', 'D'),
        ('N', 'N'),
        ('D/N', 'D/N'),
        ('PH', 'PH'),
    ]

    shift_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey( Guard,on_delete=models.CASCADE,related_name='shifts')
    shift_name = models.CharField(max_length=100)
    start_time = models.DateField()
    end_time = models.DateField()
    shift_type = models.CharField(max_length=30, choices=SHIFT_TYPE_CHOICES)

    def __str__(self):
        return f"{self.shift_name} - {self.guard}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['guard', 'start_time'],
                name='unique_guard_shift_date'
            )
        ]
    
class Salary(models.Model):
    EMPLOYEE_TYPE_CHOICES = [
        ('Guard', 'Guard'),
    ]

    MONTH_CHOICES = [
        ('January', 'January'),
        ('February', 'February'),
        ('March', 'March'),
        ('April', 'April'),
        ('May', 'May'),
        ('June', 'June'),
        ('July', 'July'),
        ('August', 'August'),
        ('September', 'September'),
        ('October', 'October'),
        ('November', 'November'),
        ('December', 'December'),
    ]

    salary_id = models.AutoField(primary_key=True)
    employee_type = models.CharField(max_length=20, choices=EMPLOYEE_TYPE_CHOICES, default='Guard')
    guard = models.ForeignKey( Guard, on_delete=models.CASCADE,related_name='salaries', null=True, blank=True)
    month = models.CharField(max_length=20, choices=MONTH_CHOICES)
    year = models.PositiveIntegerField(default=current_year)
    basic_pay = models.DecimalField(max_digits=12, decimal_places=2)
    allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, blank=True)
    payment_date = models.DateField()
    computed_from_attendance = models.BooleanField(default=False)
    attendance_days = models.PositiveIntegerField(default=0)
    daily_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def clean(self):
        if self.employee_type == 'Guard' and not self.guard:
            raise ValidationError({'guard': 'Select the guard receiving this salary.'})

    def save(self, *args, **kwargs):
        if self.employee_type == 'Guard' and self.guard:
            month_number = datetime.strptime(self.month, "%B").month
            self.computed_from_attendance = True
            self.daily_rate = self.guard.daily_rate
            self.attendance_days = Attendance.objects.filter(
                models.Q(guard=self.guard, status__in=["Present", "Late"]) |
                models.Q(replacement_guard=self.guard, status="Absent"),
                attendance_date__year=self.year,
                attendance_date__month=month_number,
                check_out_time__isnull=False,
            ).count()
            self.basic_pay = self.attendance_days * self.daily_rate

        self.net_pay = self.basic_pay + self.allowances - self.deductions
        super().save(*args, **kwargs)

    @property
    def employee(self):
        return self.guard

    @property
    def employee_number(self):
        if self.guard:
            return self.guard.guard_number

        return "-"

    def __str__(self):
        return f"{self.employee} - {self.month} {self.year} Salary"
class Incident(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Under Review', 'Under Review'),
        ('Resolved', 'Resolved'),
        ('Closed', 'Closed'),
    ]

    INCIDENT_TYPE_CHOICES = [
        ('Theft', 'Theft'),
        ('Fight', 'Fight'),
        ('Accident', 'Accident'),
        ('Fire', 'Fire'),
        ('Security Breach', 'Security Breach'),
        ('Other', 'Other'),
    ]

    TITLE_CHOICES = [
        ('Mr', 'Mr'),
        ('Ms', 'Ms'),
        ('Mrs', 'Mrs'),
    ]

    incident_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(Guard, on_delete=models.CASCADE, related_name='incidents')
    incident_date = models.DateField()
    incident_time = models.TimeField(null=True, blank=True)
    incident_type = models.CharField(max_length=50, choices=INCIDENT_TYPE_CHOICES)
    description = models.TextField()
    location = models.CharField(max_length=200)
    reporter_title = models.CharField(max_length=10, choices=TITLE_CHOICES, blank=True)
    reporter_first_name = models.CharField(max_length=100, blank=True)
    reporter_middle_name = models.CharField(max_length=100, blank=True)
    reporter_last_name = models.CharField(max_length=100, blank=True)
    involved_first_name = models.CharField(max_length=100, blank=True)
    involved_last_name = models.CharField(max_length=100, blank=True)
    further_comments = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')

    @property
    def reporter_name(self):
        name = " ".join(part for part in [
            self.reporter_title,
            self.reporter_first_name,
            self.reporter_middle_name,
            self.reporter_last_name,
        ] if part)

        return name or "-"

    @property
    def involved_full_name(self):
        return " ".join(part for part in [self.involved_first_name, self.involved_last_name] if part) or "-"

    def __str__(self):
        return f"{self.incident_type} - {self.guard.full_name}"
    
def recompute_guard_salary_for_month(guard, year, month):
    month_name = date(year, month, 1).strftime("%B")
    attendance_days = Attendance.objects.filter(
        models.Q(guard=guard, status__in=["Present", "Late"]) |
        models.Q(replacement_guard=guard, status="Absent"),
        attendance_date__year=year,
        attendance_date__month=month,
        check_out_time__isnull=False,
    ).count()

    payment_date = date(year, month, monthrange(year, month)[1])
    salary, _ = Salary.objects.get_or_create(
        employee_type="Guard",
        guard=guard,
        month=month_name,
        year=year,
        defaults={
            "basic_pay": 0,
            "allowances": 0,
            "deductions": 0,
            "payment_date": payment_date,
        },
    )

    salary.computed_from_attendance = True
    salary.attendance_days = attendance_days
    salary.daily_rate = guard.daily_rate
    salary.payment_date = payment_date
    salary.save()
    return salary


def recompute_guard_salaries(guard):
    attendance_months = Attendance.objects.filter(
        models.Q(guard=guard) | models.Q(replacement_guard=guard)
    ).dates("attendance_date", "month")

    for attendance_month in attendance_months:
        recompute_guard_salary_for_month(
            guard,
            attendance_month.year,
            attendance_month.month
        )

def recompute_payroll_for_guard_month(guard, attendance_date):
    if not guard or not attendance_date:
        return

    recompute_guard_salary_for_month(
        guard,
        attendance_date.year,
        attendance_date.month
    )

@receiver(pre_save, sender=Attendance)
def remember_old_attendance_payroll_refs(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_payroll_refs = []
        return

    old_attendance = Attendance.objects.filter(pk=instance.pk).first()
    if not old_attendance:
        instance._old_payroll_refs = []
        return

    instance._old_payroll_refs = [
        (old_attendance.guard, old_attendance.attendance_date),
        (old_attendance.replacement_guard, old_attendance.attendance_date),
    ]


@receiver(post_save, sender=Attendance)
def update_salary_after_attendance_save(sender, instance, **kwargs):
    payroll_refs = getattr(instance, "_old_payroll_refs", [])
    payroll_refs.extend([
        (instance.guard, instance.attendance_date),
        (instance.replacement_guard, instance.attendance_date),
    ])

    seen_refs = set()
    for guard, attendance_date in payroll_refs:
        if not guard or not attendance_date:
            continue

        ref_key = (guard.pk, attendance_date.year, attendance_date.month)
        if ref_key in seen_refs:
            continue

        seen_refs.add(ref_key)
        recompute_payroll_for_guard_month(guard, attendance_date)


@receiver(post_delete, sender=Attendance)
def update_salary_after_attendance_delete(sender, instance, **kwargs):
    deleting_guard_ids = getattr(_payroll_signal_state, "deleting_guard_ids", set())
    if instance.guard_id in deleting_guard_ids:
        return

    recompute_payroll_for_guard_month(instance.guard, instance.attendance_date)
    recompute_payroll_for_guard_month(instance.replacement_guard, instance.attendance_date)


@receiver(post_save, sender=Guard)
def update_salary_after_guard_rate_change(sender, instance, **kwargs):
    if not instance.guard_number:
        instance.guard_number = next_guard_number()
        Guard.objects.filter(pk=instance.pk).update(guard_number=instance.guard_number)

    if instance.status in ["Inactive", "Suspended", "Terminated", "Dismissed", "Resigned"] and instance.rfid_card:
        RFIDCard.objects.filter(pk=instance.rfid_card_id, status="Active").update(status="Inactive")

    recompute_guard_salaries(instance)


@receiver(pre_save, sender=Asset)
def remember_old_asset_assignment(sender, instance, **kwargs):
    if not instance.pk:
        instance._old_guard_id = None
        instance._old_status = None
        return

    old_asset = Asset.objects.filter(pk=instance.pk).only("guard_id", "status").first()
    instance._old_guard_id = old_asset.guard_id if old_asset else None
    instance._old_status = old_asset.status if old_asset else None


@receiver(post_save, sender=Asset)
def record_asset_assignment_history(sender, instance, created, **kwargs):
    old_guard_id = getattr(instance, "_old_guard_id", None)
    old_status = getattr(instance, "_old_status", None)

    if created:
        if instance.guard_id:
            AssetAssignmentHistory.objects.create(
                asset=instance,
                guard=instance.guard,
                notes="Asset assigned on creation.",
            )
        return

    guard_changed = old_guard_id != instance.guard_id
    status_changed = old_status != instance.status

    if guard_changed and old_guard_id:
        AssetAssignmentHistory.objects.filter(
            asset=instance,
            guard_id=old_guard_id,
            returned_date__isnull=True,
        ).update(
            returned_date=date.today(),
            condition_on_return=instance.status,
        )

    if guard_changed and instance.guard_id:
        AssetAssignmentHistory.objects.create(
            asset=instance,
            guard=instance.guard,
            notes="Asset reassigned.",
        )

    if not guard_changed and status_changed and instance.status in ["Returned", "Damaged", "Lost"]:
        AssetAssignmentHistory.objects.filter(
            asset=instance,
            guard_id=instance.guard_id,
            returned_date__isnull=True,
        ).update(
            returned_date=date.today(),
            condition_on_return=instance.status,
        )


