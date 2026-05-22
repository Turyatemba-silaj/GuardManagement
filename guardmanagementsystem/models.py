from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from datetime import date, datetime
from calendar import monthrange
import re


def current_year():
    return date.today().year


def next_employee_number():
    numbers = []

    for value in Guard.objects.exclude(employee_number__isnull=True).exclude(employee_number="").values_list("employee_number", flat=True):
        match = re.match(r"EMP(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))

    for value in Supervisor.objects.exclude(employee_number__isnull=True).exclude(employee_number="").values_list("employee_number", flat=True):
        match = re.match(r"EMP(\d+)$", value)
        if match:
            numbers.append(int(match.group(1)))

    next_number = max(numbers, default=0) + 1
    return f"EMP{next_number:03d}"

class Guard(models.Model):
    STATUS_CHOICES = [
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Suspended', 'Suspended'),
        ('Terminated', 'Terminated'),
    ]

    guard_id = models.AutoField(primary_key=True)
    employee_number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
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
    
class Supervisor(models.Model):
    supervisor_id = models.AutoField(primary_key=True)
    employee_number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    guard_id = models.ForeignKey(Guard,on_delete=models.CASCADE)
    full_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    email = models.EmailField(unique=True)
    designation = models.CharField(max_length=100)
    daily_rate = models.DecimalField(max_digits=12, decimal_places=2, default=10500)

    def __str__(self):
        return self.full_name

class Client(models.Model):
    client_id = models.AutoField(primary_key=True)
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
    guard = models.ForeignKey(
        Guard,
        on_delete=models.CASCADE,
        related_name='deployments'
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='deployments'
    )
    site_location = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Active')

    def __str__(self):
        return f"{self.guard.full_name} deployed to {self.client.client_name}"
    
class Asset(models.Model):
    STATUS_CHOICES = [
        ('Assigned', 'Assigned'),
        ('Available', 'Available'),
        ('Damaged', 'Damaged'),
        ('Lost', 'Lost'),
        ('Returned', 'Returned'),
    ]

    asset_id = models.AutoField(primary_key=True)
    guard = models.ForeignKey(
        Guard,
        on_delete=models.SET_NULL,
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
    guard = models.ForeignKey(
        Guard,
        on_delete=models.CASCADE,
        related_name='attendances'
    )
    attendance_date = models.DateField()
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Present')
    replacement_guard = models.ForeignKey(
        Guard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replacement_attendances'
    )
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
    guard = models.ForeignKey(
        Guard,
        on_delete=models.CASCADE,
        related_name='salaries',
        null=True,
        blank=True
    )
    supervisor = models.ForeignKey(
        Supervisor,
        on_delete=models.CASCADE,
        related_name='salaries',
        null=True,
        blank=True
    )
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
            return self.supervisor.employee_number

        if self.guard:
            return self.guard.employee_number

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
    guard = models.ForeignKey(
        Guard,
        on_delete=models.CASCADE,
        related_name='incidents'
    )
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
    reported_by = models.ForeignKey(
        Supervisor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reported_incidents'
    )
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
    guard = models.ForeignKey(
        Guard,
        on_delete=models.CASCADE,
        related_name='disciplinary_actions'
    )
    action_date = models.DateField()
    action_type = models.CharField(max_length=50, choices=ACTION_TYPE_CHOICES)
    description = models.TextField()
    penalty = models.CharField(max_length=200, null=True, blank=True)
    issued_by = models.ForeignKey(
        Supervisor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_disciplinary_actions'
    )

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
    request_date = models.DateField(auto_now=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
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

    def __str__(self):
        return f"{self.guard.full_name} - Advance {self.amount}"


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
    recompute_payroll_for_guard_month(instance.guard, instance.attendance_date)
    recompute_payroll_for_guard_month(instance.replacement_guard, instance.attendance_date)


@receiver(post_save, sender=Guard)
def update_salary_after_guard_rate_change(sender, instance, **kwargs):
    if not instance.employee_number:
        instance.employee_number = next_employee_number()
        Guard.objects.filter(pk=instance.pk).update(employee_number=instance.employee_number)

    recompute_guard_salaries(instance)


@receiver(post_save, sender=Supervisor)
def update_salary_after_supervisor_rate_change(sender, instance, **kwargs):
    if not instance.employee_number:
        instance.employee_number = next_employee_number()
        Supervisor.objects.filter(pk=instance.pk).update(employee_number=instance.employee_number)

    attendance_months = Attendance.objects.filter(guard=instance.guard_id).dates("attendance_date", "month")

    for attendance_month in attendance_months:
        recompute_supervisor_salary_for_month(
            instance,
            attendance_month.year,
            attendance_month.month
        )
