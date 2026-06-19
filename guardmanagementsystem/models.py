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


def next_guard_number():
    numbers = []

    for value in Guard.objects.exclude(guard_number__isnull=True).exclude(guard_number="").values_list("guard_number", flat=True):
        match = re.match(r"GUARD(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))

    next_number = max(numbers, default=0) + 1
    return f"GUARD{next_number:03d}"


def next_supervisor_number():
    numbers = []

    for value in Supervisor.objects.exclude(supervisor_number__isnull=True).exclude(supervisor_number="").values_list("supervisor_number", flat=True):
        match = re.match(r"SUP(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))

    next_number = max(numbers, default=0) + 1
    return f"SUP{next_number:03d}"


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
    supervisor = models.ForeignKey('Supervisor', on_delete=models.SET_NULL, null=True, blank=True, related_name='supervised_guards')
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
    
class Deployment(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled'),
    ]

    deployment_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(Guard, on_delete=models.CASCADE,related_name='deployments')
    client = models.ForeignKey(Client,on_delete=models.CASCADE,related_name='deployments')
    site_location = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')

    def __str__(self):
        return f"{self.guard.full_name} deployed to {self.client.client_name}"

class Supervisor(models.Model):
    supervisor_id = models.AutoField(primary_key=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='supervisor_profile')
    full_name = models.CharField(max_length=100)
    supervisor_number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    guard_id = models.ForeignKey(Guard, on_delete=models.CASCADE, related_name='supervisor_profiles')
    deployment_id = models.ForeignKey(Deployment,on_delete=models.CASCADE,null=True,blank=True)
    daily_rate = models.DecimalField(max_digits=12, decimal_places=2, default=10000)
    phone = models.CharField(max_length=20)
    email = models.EmailField(max_length=100, blank=True, null=True)
    designation = models.CharField(max_length=100, blank=True)
    advance_notifications = models.PositiveIntegerField(default=0)
    client_notifications = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.full_name

    @property
    def employee_number(self):
        return self.supervisor_number


class ClientCommunication(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('Service Request', 'Service Request'),
        ('Complaint', 'Complaint'),
        ('Feedback', 'Feedback'),
        ('Incident Report', 'Incident Report'),
        ('Communication', 'Communication'),
    ]

    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Under Review', 'Under Review'),
        ('Resolved', 'Resolved'),
        ('Closed', 'Closed'),
    ]

    communication_id = models.AutoField(primary_key=True)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='communications')
    review_supervisor = models.ForeignKey(
        Supervisor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='client_communications'
    )
    message_type = models.CharField(max_length=30, choices=MESSAGE_TYPE_CHOICES)
    subject = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    supervisor_response = models.TextField(blank=True)

    def clean(self):
        super().clean()
        if self.client_id and not self.review_supervisor:
            self.review_supervisor = get_client_review_supervisor(self.client)

        if not self.review_supervisor:
            raise ValidationError("This client has no deployed guard with an assigned supervisor for communication review.")

    def save(self, *args, **kwargs):
        previous_supervisor_id = None
        if self.pk:
            previous_supervisor_id = ClientCommunication.objects.filter(pk=self.pk).values_list("review_supervisor_id", flat=True).first()

        if self.client_id:
            self.review_supervisor = self.review_supervisor or get_client_review_supervisor(self.client)

        super().save(*args, **kwargs)
        refresh_supervisor_client_notifications(self.review_supervisor_id)
        if previous_supervisor_id and previous_supervisor_id != self.review_supervisor_id:
            refresh_supervisor_client_notifications(previous_supervisor_id)

    def __str__(self):
        return f"{self.client} - {self.message_type} - {self.subject}"


def get_client_review_supervisor(client):
    deployment = Deployment.objects.select_related("guard__supervisor").filter(
        client=client,
        status="Active",
        guard__supervisor__isnull=False,
    ).order_by("-start_date", "-deployment_id").first()
    return deployment.guard.supervisor if deployment else None


def refresh_supervisor_client_notifications(supervisor_id):
    if not supervisor_id:
        return

    pending_count = ClientCommunication.objects.filter(
        review_supervisor_id=supervisor_id,
        status="Pending",
    ).count()
    Supervisor.objects.filter(supervisor_id=supervisor_id).update(client_notifications=pending_count)
   

    
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

    def __str__(self):
        return f"{self.asset_name} - {self.serial_number}"
    
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
    swiped_by_supervisor = models.ForeignKey(Supervisor, on_delete=models.SET_NULL, null=True, blank=True, related_name='swiped_attendances')
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
        ('Supervisor', 'Supervisor'),
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
    supervisor = models.ForeignKey( Supervisor, on_delete=models.CASCADE,related_name='salaries', null=True, blank=True)
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

        if self.employee_type == 'Supervisor' and not self.supervisor:
            raise ValidationError({'supervisor': 'Select the supervisor receiving this salary.'})

        if self.guard and self.supervisor:
            raise ValidationError('Select either a guard or a supervisor, not both.')

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
            ).count()
            self.basic_pay = self.attendance_days * self.daily_rate

        if self.employee_type == 'Supervisor' and self.supervisor:
            month_number = datetime.strptime(self.month, "%B").month
            self.computed_from_attendance = True
            self.daily_rate = self.supervisor.daily_rate
            self.attendance_days = Attendance.objects.filter(
                models.Q(guard=self.supervisor.guard_id, status__in=["Present", "Late"]) |
                models.Q(replacement_guard=self.supervisor.guard_id, status="Absent"),
                attendance_date__year=self.year,
                attendance_date__month=month_number,
            ).count()
            self.basic_pay = self.attendance_days * self.daily_rate

        self.net_pay = self.basic_pay + self.allowances - self.deductions
        super().save(*args, **kwargs)

    @property
    def employee(self):
        return self.supervisor if self.employee_type == 'Supervisor' else self.guard

    @property
    def employee_number(self):
        if self.employee_type == 'Supervisor' and self.supervisor:
            return self.supervisor.supervisor_number

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
    reported_by = models.ForeignKey(Supervisor,on_delete=models.SET_NULL, null=True,blank=True, related_name='reported_incidents')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')

    @property
    def reporter_name(self):
        name = " ".join(part for part in [
            self.reporter_title,
            self.reporter_first_name,
            self.reporter_middle_name,
            self.reporter_last_name,
        ] if part)

        return name or self.reported_by or "-"

    @property
    def involved_full_name(self):
        return " ".join(part for part in [self.involved_first_name, self.involved_last_name] if part) or "-"

    def __str__(self):
        return f"{self.incident_type} - {self.guard.full_name}"
    
class DisciplinaryAction(models.Model):
    ACTION_TYPE_CHOICES = [
        ('Warning', 'Warning'),
        ('Suspension', 'Suspension'),
        ('Fine', 'Fine'),
        ('Dismissal', 'Dismissal'),
        ('Other', 'Other'),
    ]

    disciplinary_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(Guard, on_delete=models.CASCADE, related_name='disciplinary_actions')
    action_date = models.DateField()
    action_type = models.CharField(max_length=50, choices=ACTION_TYPE_CHOICES)
    description = models.TextField()
    penalty = models.CharField(max_length=200, null=True, blank=True)
    issued_by = models.ForeignKey(Supervisor,on_delete=models.SET_NULL,null=True,blank=True, related_name='issued_disciplinary_actions')

    def __str__(self):
        return f"{self.guard.full_name} - {self.action_type}"
    
class AdvanceRequest(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Paid', 'Paid'),
    ]

    advance_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(
        Guard,
        on_delete=models.CASCADE,
        related_name='advance_requests'
    )
    review_supervisor = models.ForeignKey(
        Supervisor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='advance_notifications_requests'
    )
    request_date = models.DateField(auto_now=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    installment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    recovery_period_months = models.PositiveIntegerField(default=6)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    approved_by = models.ForeignKey(
        Supervisor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_advance_requests'
    )
    approved_date = models.DateField(null=True, blank=True)
    approval_reason = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    def calculate_gross_pay(self):
        if not self.guard:
            return Decimal("0.00")

        pay_date = self.request_date or date.today()
        attendance_days = Attendance.objects.filter(
            models.Q(guard=self.guard, status__in=["Present", "Late"]) |
            models.Q(replacement_guard=self.guard, status="Absent"),
            models.Q(check_in_time__isnull=False) | models.Q(check_out_time__isnull=False),
            attendance_date__year=pay_date.year,
            attendance_date__month=pay_date.month,
        ).values("attendance_date").distinct().count()

        gross_pay = Decimal(attendance_days) * Decimal(self.guard.daily_rate or 0)
        return gross_pay.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def calculate_installment_amount(self):
        return (self.calculate_gross_pay() * Decimal("0.20")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def clean(self):
        super().clean()
        if self.guard and not self.review_supervisor:
            self.review_supervisor = self.guard.supervisor

        if not self.recovery_period_months:
            self.recovery_period_months = 6

        self.installment_amount = self.calculate_installment_amount()
        recoverable_amount = self.installment_amount * Decimal(self.recovery_period_months)

        if self.amount and self.installment_amount <= 0:
            raise ValidationError("This guard has no swipe-attendance gross pay for the current payroll month, so an advance installment cannot be calculated.")

        if self.amount and self.amount > recoverable_amount:
            raise ValidationError({
                "amount": f"Advance amount must not exceed {recoverable_amount} so it can be recovered within {self.recovery_period_months} month(s)."
            })

    def save(self, *args, **kwargs):
        previous_supervisor_id = None
        if self.pk:
            previous_supervisor_id = AdvanceRequest.objects.filter(pk=self.pk).values_list("review_supervisor_id", flat=True).first()

        if self.guard:
            self.review_supervisor = self.guard.supervisor

        self.recovery_period_months = self.recovery_period_months or 6
        self.installment_amount = self.calculate_installment_amount()
        super().save(*args, **kwargs)
        refresh_supervisor_advance_notifications(self.review_supervisor_id)
        if previous_supervisor_id and previous_supervisor_id != self.review_supervisor_id:
            refresh_supervisor_advance_notifications(previous_supervisor_id)

    def __str__(self):
        return f"{self.guard.full_name} - Advance {self.amount}"


def refresh_supervisor_advance_notifications(supervisor_id):
    if not supervisor_id:
        return

    pending_count = AdvanceRequest.objects.filter(
        review_supervisor_id=supervisor_id,
        status="Pending",
    ).count()
    Supervisor.objects.filter(supervisor_id=supervisor_id).update(advance_notifications=pending_count)


def recompute_guard_salary_for_month(guard, year, month):
    month_name = date(year, month, 1).strftime("%B")
    attendance_days = Attendance.objects.filter(
        models.Q(guard=guard, status__in=["Present", "Late"]) |
        models.Q(replacement_guard=guard, status="Absent"),
        attendance_date__year=year,
        attendance_date__month=month,
    ).count()

    payment_date = date(year, month, monthrange(year, month)[1])
    salary, _ = Salary.objects.get_or_create(
        employee_type="Guard",
        guard=guard,
        supervisor=None,
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


def recompute_supervisor_salary_for_month(supervisor, year, month):
    month_name = date(year, month, 1).strftime("%B")
    payment_date = date(year, month, monthrange(year, month)[1])
    salary, _ = Salary.objects.get_or_create(
        employee_type="Supervisor",
        supervisor=supervisor,
        guard=None,
        month=month_name,
        year=year,
        defaults={
            "basic_pay": 0,
            "allowances": 0,
            "deductions": 0,
            "payment_date": payment_date,
        },
    )

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

        for supervisor in Supervisor.objects.filter(guard_id=guard):
            recompute_supervisor_salary_for_month(
                supervisor,
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

    for supervisor in Supervisor.objects.filter(guard_id=guard):
        recompute_supervisor_salary_for_month(
            supervisor,
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


@receiver(post_delete, sender=AdvanceRequest)
def update_supervisor_advance_notifications_after_delete(sender, instance, **kwargs):
    refresh_supervisor_advance_notifications(instance.review_supervisor_id)


@receiver(post_delete, sender=ClientCommunication)
def update_supervisor_client_notifications_after_delete(sender, instance, **kwargs):
    refresh_supervisor_client_notifications(instance.review_supervisor_id)


@receiver(post_save, sender=Guard)
def update_salary_after_guard_rate_change(sender, instance, **kwargs):
    if not instance.guard_number:
        instance.guard_number = next_guard_number()
        Guard.objects.filter(pk=instance.pk).update(guard_number=instance.guard_number)

    if instance.status in ["Inactive", "Suspended", "Terminated", "Dismissed", "Resigned"] and instance.rfid_card:
        RFIDCard.objects.filter(pk=instance.rfid_card_id, status="Active").update(status="Inactive")

    recompute_guard_salaries(instance)


@receiver(post_save, sender=Supervisor)
def update_salary_after_supervisor_rate_change(sender, instance, **kwargs):
    if not instance.supervisor_number:
        instance.supervisor_number = next_supervisor_number()
        Supervisor.objects.filter(pk=instance.pk).update(supervisor_number=instance.supervisor_number)

    attendance_months = Attendance.objects.filter(guard=instance.guard_id).dates("attendance_date", "month")

    for attendance_month in attendance_months:
        recompute_supervisor_salary_for_month(
            instance,
            attendance_month.year,
            attendance_month.month
        )
