from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages 
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace


from .models import (
    Asset,
    AssetAssignmentHistory,
    Attendance,
    AttendanceSwipe,
    AuditLog,
    Client,
    Contract,
    Deployment,
    DeploymentGuard,
    Guard,
    Incident,
    IoTDevice,
    RFIDCard,
    Salary,
    Shift,
    recompute_guard_salary_for_month,
)
from .role_access import user_can_access
from .forms import (
    AssetForm,
    ClientForm,
    ContractForm,
    DeploymentForm,
    DeploymentGuardForm,
    DeploymentGuardBulkForm,
    EmailOrUsernameAuthenticationForm,
    GuardForm,
    IncidentForm,
    IoTDeviceForm,
    ProgramGuardForm,
    RFIDCardForm,
    SalaryForm,
    ShiftForm,
    SignupForm,
    UserCreateForm,
    UserEditForm,
)

MONEY_PLACES = Decimal("0.01")
MONTH_NUMBERS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

DEPLOYMENT_STATUS_ACTIVE = "Active"
DEPLOYMENT_STATUS_RUNNING = "Running"
DEPLOYMENT_STATUS_ON_DEPLOYMENT = "On Deployment"
DEPLOYMENT_STATUS_AVAILABLE = "Available"
DEPLOYMENT_STATUS_HAS_NO_DEPLOYMENT = "Has No Deployment"
GUARD_DEPLOYMENT_STATUS_ON_SITE = "On Site"
GUARD_DEPLOYMENT_STATUS_AVAILABLE = "Available for Deployment"
ACTIVE_DEPLOYMENT_STATUSES = [
    DEPLOYMENT_STATUS_ACTIVE,
    DEPLOYMENT_STATUS_RUNNING,
    DEPLOYMENT_STATUS_ON_DEPLOYMENT,
    DEPLOYMENT_STATUS_AVAILABLE,
    DEPLOYMENT_STATUS_HAS_NO_DEPLOYMENT,
]


def money(value):
    return Decimal(value or 0).quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def pdf_escape(value):
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_pdf_text(text, width=88):
    words = str(text or "-").split()
    lines = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    return lines or ["-"]


def build_simple_pdf(title, sections):
    pages = []
    lines = []

    def add_line(text="", size=10, bold=False):
        nonlocal lines
        if len(lines) >= 45:
            pages.append(lines)
            lines = []
        lines.append((text, size, bold))

    add_line("GUARD MANAGEMENT SYSTEM", 16, True)
    add_line(title, 13, True)
    add_line(f"Generated: {timezone.localtime().strftime('%Y-%m-%d %I:%M %p')}", 9, False)
    add_line("")

    for section_title, rows in sections:
        add_line(section_title.upper(), 11, True)
        for label, value in rows:
            text = f"{label}: {value or '-'}"
            for wrapped_line in wrap_pdf_text(text):
                add_line(wrapped_line)
        add_line("")

    add_line("AUTHORIZATION AND CLOSURE", 11, True)
    add_line("Prepared By: ______________________________    Signature: ______________________________")
    add_line("Reviewed By: ______________________________    Signature: ______________________________")
    add_line("Approved By: ______________________________    Date Closed: ____________________________")

    if lines:
        pages.append(lines)

    objects = []
    page_refs = []
    font_regular_ref = 3
    font_bold_ref = 4

    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append("<< /Type /Pages /Kids [] /Count 0 >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    for page_lines in pages:
        content_parts = ["BT", "50 790 Td"]
        current_size = None
        current_font = None

        for text, size, bold in page_lines:
            font = "F2" if bold else "F1"
            if current_size != size or current_font != font:
                content_parts.append(f"/{font} {size} Tf")
                current_size = size
                current_font = font
            content_parts.append(f"({pdf_escape(text)}) Tj")
            content_parts.append("0 -16 Td")

        content_parts.append("ET")
        content = "\n".join(content_parts).encode("latin-1", errors="replace")
        content_ref = len(objects) + 1
        page_ref = len(objects) + 2
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content.decode('latin-1')}\nendstream")
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_regular_ref} 0 R /F2 {font_bold_ref} 0 R >> >> "
            f"/Contents {content_ref} 0 R >>"
        )
        page_refs.append(page_ref)

    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{ref} 0 R' for ref in page_refs)}] /Count {len(page_refs)} >>"

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1", errors="replace"))

    xref_at = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode("ascii")
    )
    return bytes(pdf)


def welcome(request):
    return render(request, "guardmanagementsystem/welcome.html")

def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /signin/",
        "Disallow: /signup/",
        "Disallow: /forgot-password/",
        "Disallow: /reset-password/",
        "Disallow: /dashboard/",
        "Allow: /$",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")



def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0]
    return request.META.get("REMOTE_ADDR")


def log_action(request, action, description):
    user = request.user if request.user.is_authenticated else None
    AuditLog.objects.create(
        user=user,
        action=action,
        description=description,
        ip_address=get_client_ip(request)
    )


def signup(request):
    if not settings.PUBLIC_SIGNUP_ENABLED:
        raise Http404("Public account registration is not available.")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, "Account created successfully. You can now sign in.")
            AuditLog.objects.create(
                user=user,
                action="SIGNUP",
                description="New user account was created.",
                ip_address=get_client_ip(request)
            )
            return redirect("signin")
    else:
        form = SignupForm()

    return render(request, "guardmanagementsystem/auth_form.html", {
        "form": form,
        "title": "Create Account",
        "button_text": "Sign Up",
        "helper_link": "signin",
        "helper_text": "Already have an account? Sign in"
    })


def user_home_url_name(user):
    if user_is_guard(user):
        return "iot_swipe_attendance"
    if user_can_access(user, "dashboard"):
        return "dashboard"
    return "iot_swipe_attendance"


def signin(request):
    if request.user.is_authenticated and request.method == "GET":
        return redirect(user_home_url_name(request.user))

    if request.method == "POST":
        form = EmailOrUsernameAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            log_action(request, "SIGNIN", "User signed in.")
            messages.success(request, "Signed in successfully.")
            return redirect(user_home_url_name(user))
    else:
        form = EmailOrUsernameAuthenticationForm()

    for field in form.fields.values():
        field.widget.attrs.update({"class": "form-control"})

    return render(request, "guardmanagementsystem/auth_form.html", {
        "form": form,
        "title": "Sign In",
        "button_text": "Sign In",
        "helper_link": "forgot_password" if settings.PUBLIC_PASSWORD_RESET_ENABLED else None,
        "helper_text": "Forgot password?" if settings.PUBLIC_PASSWORD_RESET_ENABLED else ""
    })


@login_required
def signout(request):
    log_action(request, "LOGOUT", "User signed out.")
    logout(request)
    messages.success(request, "Signed out successfully.")
    return redirect("signin")


def forgot_password(request):
    if not settings.PUBLIC_PASSWORD_RESET_ENABLED:
        raise Http404("Public password reset is not available.")

    if request.method == "POST":
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            users = User.objects.filter(email__iexact=email, is_active=True)
            for user in users:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                reset_url = request.build_absolute_uri(
                    f"/reset-password/{uid}/{token}/"
                )
                send_mail(
                    "Reset your Guard Management password",
                    f"Use this link to reset your password: {reset_url}",
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
                AuditLog.objects.create(
                    user=user,
                    action="PASSWORD_RESET_REQUEST",
                    description="Password reset link was requested.",
                    ip_address=get_client_ip(request)
                )
            messages.success(request, "If the email exists, a reset link has been sent.")
            return redirect("signin")
    else:
        form = PasswordResetForm()

    for field in form.fields.values():
        field.widget.attrs.update({"class": "form-control"})

    return render(request, "guardmanagementsystem/auth_form.html", {
        "form": form,
        "title": "Forgot Password",
        "button_text": "Send Reset Link",
        "helper_link": "signin",
        "helper_text": "Back to sign in"
    })


def reset_password(request, uidb64, token):
    if not settings.PUBLIC_PASSWORD_RESET_ENABLED:
        raise Http404("Public password reset is not available.")

    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if not user or not default_token_generator.check_token(user, token):
        messages.error(request, "Password reset link is invalid or expired.")
        return redirect("forgot_password")

    if request.method == "POST":
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            AuditLog.objects.create(
                user=user,
                action="PASSWORD_RESET",
                description="User reset their password.",
                ip_address=get_client_ip(request)
            )
            messages.success(request, "Password reset successfully. Please sign in.")
            return redirect("signin")
    else:
        form = SetPasswordForm(user)

    for field in form.fields.values():
        field.widget.attrs.update({"class": "form-control"})

    return render(request, "guardmanagementsystem/auth_form.html", {
        "form": form,
        "title": "Reset Password",
        "button_text": "Reset Password",
        "helper_link": "signin",
        "helper_text": "Back to sign in"
    })

def send_incident_notification(incident):
    return False, "Incident notification is available in the portal."


def matching_deployment_queryset_for_guard(guard, work_date, site=None, client_name=None):
    assignment_deployment_ids = DeploymentGuard.objects.filter(
        guard=guard,
        deployment_date=work_date,
        deployment__status__in=ACTIVE_DEPLOYMENT_STATUSES,
        deployment__start_date__lte=work_date,
    ).filter(
        Q(deployment__end_date__isnull=True) | Q(deployment__end_date__gte=work_date),
    ).values_list("deployment_id", flat=True)

    deployments = Deployment.objects.select_related("client", "contract").filter(
        deployment_id__in=assignment_deployment_ids
    )

    if site:
        deployments = deployments.filter(site_location=site)

    if client_name:
        deployments = deployments.filter(client__client_name__iexact=client_name)

    return deployments


def has_active_deployment(guard, work_date, site=None):
    if not guard or not work_date:
        return False
    return matching_deployment_queryset_for_guard(guard, work_date, site).exists()


def get_matching_deployment(guard, work_date, site=None, client_name=None):
    if not guard or not work_date:
        return None
    return matching_deployment_queryset_for_guard(guard, work_date, site, client_name).first()


def get_deployment_from_attendance_swipes(attendance):
    device_ids = [
        swipe.iot_device_id
        for swipe in attendance.swipes.all()
        if swipe.iot_device_id
    ]
    if not device_ids:
        return None

    return (
        Deployment.objects.select_related("client", "contract")
        .filter(
            contract__iot_device_id__in=device_ids,
            start_date__lte=attendance.attendance_date,
        )
        .filter(
            Q(end_date__isnull=True) | Q(end_date__gte=attendance.attendance_date)
        )
        .order_by("-deployment_id")
        .first()
    )


def get_attendance_deployment(attendance, site=None, client_name=None):
    deployment = get_matching_deployment(attendance.guard, attendance.attendance_date, site, client_name)
    if deployment:
        return deployment
    return get_deployment_from_attendance_swipes(attendance)



def active_assignments_for_iot_device(device, work_date=None):
    work_date = work_date or timezone.localdate()
    if not device:
        return DeploymentGuard.objects.none()

    assignments = DeploymentGuard.objects.select_related(
        "deployment",
        "deployment__client",
        "deployment__contract",
        "guard",
    ).filter(
        deployment_date=work_date,
        deployment__status__in=ACTIVE_DEPLOYMENT_STATUSES,
        deployment__start_date__lte=work_date,
        guard__status="Active",
        guard__rfid_card__status="Active",
    ).filter(
        Q(deployment__end_date__isnull=True) | Q(deployment__end_date__gte=work_date),
    )

    return assignments.filter(deployment__contract__iot_device=device).distinct()



def find_active_assignments_by_card(card_id, site_location=None, device=None, work_date=None):
    card_id = str(card_id or "").strip()
    work_date = work_date or timezone.localdate()
    site_location = str(site_location or "").strip()

    if not card_id:
        return []

    if device:
        assignments = active_assignments_for_iot_device(device, work_date)
    else:
        assignments = DeploymentGuard.objects.select_related(
            "deployment",
            "deployment__client",
            "deployment__contract",
            "guard",
        ).filter(
            deployment_date=work_date,
            deployment__status__in=ACTIVE_DEPLOYMENT_STATUSES,
            deployment__start_date__lte=work_date,
            guard__status="Active",
            guard__rfid_card__status="Active",
        ).filter(
            Q(deployment__end_date__isnull=True) | Q(deployment__end_date__gte=work_date),
        )

    if site_location:
        assignments = assignments.filter(deployment__site_location=site_location)

    return list(assignments.filter(
        Q(guard__rfid_card__card_uid=card_id) |
        Q(guard__rfid_card__card_number=card_id)
    ).distinct().order_by("deployment_guard_id"))


def iot_device_swipe_options_data(device):
    assignments = active_assignments_for_iot_device(device)
    sites = list(
        assignments.exclude(deployment__site_location="")
        .values_list("deployment__site_location", flat=True)
        .distinct()
        .order_by("deployment__site_location")
    )
    cards = []
    seen_cards = set()
    for assignment in assignments.order_by("guard__full_name", "deployment__site_location"):
        card = assignment.guard.rfid_card
        if not card or card.status != "Active" or card.rfid_card_id in seen_cards:
            continue
        seen_cards.add(card.rfid_card_id)
        cards.append({
            "value": card.card_uid,
            "label": f"{card.card_number} - {assignment.guard.guard_number or '-'} - {assignment.guard.full_name}",
            "card_number": card.card_number,
        })

    return {"sites": sites, "cards": cards}


def infer_single_site_for_iot_device(device):
    sites = iot_device_swipe_options_data(device)["sites"]
    return sites[0] if len(sites) == 1 else ""


def contract_guard_limit_exceeded_for_deployment(deployment, work_date):
    if not deployment or not deployment.contract_id or not work_date:
        return False

    active_assignments = DeploymentGuard.objects.filter(
        deployment__contract=deployment.contract,
        deployment__status__in=ACTIVE_DEPLOYMENT_STATUSES,
        deployment__start_date__lte=work_date,
        deployment_date=work_date,
    ).filter(
        Q(deployment__end_date__isnull=True) | Q(deployment__end_date__gte=work_date),
    )
    assigned_guard_count = active_assignments.values("guard_id").distinct().count()
    return assigned_guard_count > deployment.contract.number_of_guards


def get_or_create_deployment_for_schedule(guard, client, site_location, start_date, end_date):
    deployment = Deployment.objects.filter(
        client=client,
        site_location=site_location,
        start_date=start_date,
        end_date=end_date,
    ).order_by("-deployment_id").first()

    if deployment:
        created = False
    else:
        deployment = Deployment.objects.create(
            client=client,
            site_location=site_location,
            start_date=start_date,
            end_date=end_date,
            status="Active",
        )
        created = True

    if deployment.status not in ACTIVE_DEPLOYMENT_STATUSES:
        deployment.status = DEPLOYMENT_STATUS_ACTIVE
        deployment.save(update_fields=["status"])

    return deployment, created


def ensure_guard_deployment_for_date(deployment, guard, deployment_date, shift_type='D'):
    assignment, created = DeploymentGuard.objects.get_or_create(
        deployment=deployment,
        guard=guard,
        deployment_date=deployment_date,
        defaults={
            "status": GUARD_DEPLOYMENT_STATUS_AVAILABLE,
            "shift_type": shift_type or 'D',
        },
    )

    update_fields = []
    if assignment.status == "Cancelled":
        assignment.status = GUARD_DEPLOYMENT_STATUS_AVAILABLE
        update_fields.append("status")
    if shift_type and assignment.shift_type != shift_type:
        assignment.shift_type = shift_type
        update_fields.append("shift_type")
    if update_fields:
        assignment.save(update_fields=update_fields)

    return assignment, created

def update_deployment_after_iot_swipe(guard, work_date, site_location, status, swipe_time=None):
    deployment = get_matching_deployment(guard, work_date, site_location)
    if not deployment:
        return None

    assignment = DeploymentGuard.objects.filter(
        deployment=deployment,
        guard=guard,
        deployment_date=work_date,
    ).order_by("-deployment_guard_id").first()

    if assignment:
        if assignment.status != status:
            assignment.status = status
            assignment.save(update_fields=["status"])

    return deployment


def recent_attendance_records():
    return Attendance.objects.select_related(
        "guard",
        "replacement_guard",
    ).prefetch_related("swipes").filter(
        Q(check_in_time__isnull=False) | Q(check_out_time__isnull=False)
    ).order_by("-attendance_date", "-check_out_time", "-check_in_time", "-attendance_id")


def build_recent_attendance_salary_rows(guard_id=None, year=None, month=None):
    salary_rows_by_guard_month = {}
    guard_id = str(guard_id or "").strip()
    year = clean_schedule_period_value(year, 0) if year else None
    month = clean_schedule_period_value(month, 0) if month else None

    for attendance in recent_attendance_records().filter(check_out_time__isnull=False).order_by("attendance_date"):
        earning_guard = attendance.replacement_guard or attendance.guard
        if guard_id and str(earning_guard.guard_id) != guard_id:
            continue

        if year and attendance.attendance_date.year != year:
            continue

        if month and attendance.attendance_date.month != month:
            continue

        key = (earning_guard.guard_id, attendance.attendance_date.year, attendance.attendance_date.month)

        if key not in salary_rows_by_guard_month:
            salary_rows_by_guard_month[key] = SimpleNamespace(
                salary_id=f"ATT-{earning_guard.guard_id}-{attendance.attendance_date.year}-{attendance.attendance_date.month:02d}",
                attendance_payslip=True,
                guard_id=earning_guard.guard_id,
                month_number=attendance.attendance_date.month,
                employee_type="Guard",
                guard=earning_guard,
                employee=earning_guard,
                employee_number=earning_guard.guard_number,
                month=attendance.attendance_date.strftime("%B"),
                year=attendance.attendance_date.year,
                attendance_days=0,
                daily_rate=earning_guard.daily_rate,
                basic_pay=money(0),
                allowances=money(0),
                deductions=money(0),
                net_pay=money(0),
                payment_date=attendance.attendance_date,
                first_attendance_date=attendance.attendance_date,
                last_attendance_date=attendance.attendance_date,
                last_swipe_time=attendance.check_out_time or attendance.check_in_time,
            )

        salary_row = salary_rows_by_guard_month[key]
        salary_row.attendance_days += 1
        salary_row.basic_pay = money(salary_row.attendance_days * salary_row.daily_rate)
        salary_row.net_pay = money(salary_row.basic_pay + salary_row.allowances - salary_row.deductions)
        salary_row.payment_date = max(salary_row.payment_date, attendance.attendance_date)
        salary_row.first_attendance_date = min(salary_row.first_attendance_date, attendance.attendance_date)
        salary_row.last_attendance_date = max(salary_row.last_attendance_date, attendance.attendance_date)
        salary_row.last_swipe_time = attendance.check_out_time or attendance.check_in_time or salary_row.last_swipe_time

    return sorted(
        salary_rows_by_guard_month.values(),
        key=lambda salary: (salary.year, salary.payment_date, str(salary.employee)),
        reverse=True,
    )


def build_guard_month_attendance_summary(guard, year, month):
    month_start = date(year, month, 1)
    month_end = date(year, month, monthrange(year, month)[1])
    attendances = (
        recent_attendance_records()
        .filter(
            Q(guard=guard, status__in=["Present", "Late"]) |
            Q(replacement_guard=guard, status="Absent"),
            attendance_date__gte=month_start,
            attendance_date__lte=month_end,
            check_out_time__isnull=False,
        )
        .order_by("attendance_date", "check_in_time", "attendance_id")
    )

    rows = []
    for attendance in attendances:
        deployment = get_attendance_deployment(attendance)
        shift = get_attendance_shift(attendance, deployment)
        rows.append(SimpleNamespace(
            attendance=attendance,
            deployment_date=attendance.attendance_date,
            client=deployment.client.client_name if deployment else "-",
            contract=deployment.contract.contract_number if deployment and deployment.contract else "-",
            site_location=deployment.site_location if deployment else "-",
            shift_type=get_shift_code(shift) if shift else "-",
            amount=money(guard.daily_rate),
            role="Replacement" if attendance.replacement_guard_id == guard.guard_id else "Assigned",
        ))

    total_shifts_done = len(rows)
    return SimpleNamespace(
        guard=guard,
        year=year,
        month=month,
        month_name=month_start.strftime("%B"),
        rows=rows,
        total_shifts_done=total_shifts_done,
        daily_rate=money(guard.daily_rate),
        total_expected_amount=money(total_shifts_done * guard.daily_rate),
    )


def user_is_guard(user):
    return user.is_authenticated and user.groups.filter(name="Guard").exists()


def get_user_guard(user):
    if not user.is_authenticated:
        return None
    return Guard.objects.filter(user=user).first()


def get_user_client(user):
    if not user.is_authenticated:
        return None
    return Client.objects.filter(user=user).first()



HOURLY_SWIPE_COUNT = 12
HOURLY_SWIPE_INTERVAL = timedelta(hours=1)
HOURLY_SWIPE_TOLERANCE = timedelta(minutes=30)


def attendance_datetime(attendance, swipe_time):
    if not swipe_time:
        return None
    return timezone.make_aware(datetime.combine(attendance.attendance_date, swipe_time))


def expected_hourly_swipe_times(attendance):
    start_at = attendance_datetime(attendance, attendance.check_in_time)
    if not start_at:
        return []
    return [
        start_at + (HOURLY_SWIPE_INTERVAL * slot_number)
        for slot_number in range(HOURLY_SWIPE_COUNT)
    ]


def decorate_attendance_swipe_status(attendance):
    swipes = list(attendance.swipes.all())
    now = timezone.localtime()
    checkout_at = attendance_datetime(attendance, attendance.check_out_time)
    review_until = checkout_at or now
    expected_rows = []
    missed_rows = []

    for expected_at in expected_hourly_swipe_times(attendance):
        window_start = expected_at - HOURLY_SWIPE_TOLERANCE
        window_end = expected_at + HOURLY_SWIPE_TOLERANCE
        matching_swipe = next(
            (
                swipe for swipe in swipes
                if window_start <= timezone.localtime(swipe.swipe_time) <= window_end
            ),
            None,
        )
        is_due = expected_at <= review_until
        status = "Recorded" if matching_swipe else ("Missed" if is_due else "Pending")
        row = SimpleNamespace(
            expected_at=expected_at,
            actual_at=timezone.localtime(matching_swipe.swipe_time) if matching_swipe else None,
            status=status,
        )
        expected_rows.append(row)
        if status == "Missed":
            missed_rows.append(row)

    attendance.recorded_swipe_count = len(swipes)
    attendance.expected_swipe_count = HOURLY_SWIPE_COUNT
    attendance.expected_swipes = expected_rows
    attendance.missed_swipes = missed_rows
    attendance.missed_swipe_count = len(missed_rows)
    attendance.missed_swipe_label = ", ".join(
        row.expected_at.strftime("%H:%M") for row in missed_rows
    ) or "-"
    return attendance


def create_attendance_swipe(attendance, device=None, swipe_type="hourly"):
    swipe_count = attendance.swipes.count()
    if swipe_count >= HOURLY_SWIPE_COUNT:
        return None

    swipe = AttendanceSwipe.objects.create(
        attendance=attendance,
        iot_device=device,
        swipe_time=timezone.now(),
        swipe_type=swipe_type,
    )
    return swipe


def close_attendance_for_checkout(attendance, guard, work_date, site_location, device=None, checkout_time=None):
    current_time = timezone.localtime().time()
    checkout_time = checkout_time or current_time
    if not attendance.check_in_time:
        attendance.check_in_time = checkout_time
    attendance.check_out_time = checkout_time
    attendance.status = "Present"
    attendance.save()
    update_deployment_after_iot_swipe(guard, work_date, site_location, GUARD_DEPLOYMENT_STATUS_AVAILABLE, checkout_time)
    recompute_guard_salary_for_month(guard, work_date.year, work_date.month)
    return attendance


def close_attendance_from_existing_swipes(attendance, guard, work_date, site_location, device=None):
    swipes = list(attendance.swipes.order_by("swipe_time", "swipe_id"))
    if len(swipes) < HOURLY_SWIPE_COUNT:
        return False

    checkout_swipe = swipes[HOURLY_SWIPE_COUNT - 1]
    if checkout_swipe.swipe_type != "check_out":
        checkout_swipe.swipe_type = "check_out"
        checkout_swipe.save(update_fields=["swipe_type"])

    extra_swipe_ids = [swipe.swipe_id for swipe in swipes[HOURLY_SWIPE_COUNT:]]
    if extra_swipe_ids:
        AttendanceSwipe.objects.filter(swipe_id__in=extra_swipe_ids).delete()

    checkout_at = timezone.localtime(checkout_swipe.swipe_time).time()
    close_attendance_for_checkout(attendance, guard, work_date, site_location, device, checkout_at)
    return True

def record_iot_attendance(card_id, site_location, action="check_in", replacement_guard_id=None, device=None):
    card_id = str(card_id or "").strip()
    site_location = str(site_location or "").strip()
    action = str(action or "auto").strip().lower()

    if not card_id:
        return False, "RFID card number is required.", None

    if not site_location:
        return False, "Site location is required.", None

    today = timezone.localdate()
    current_time = timezone.localtime().time()
    assignments = find_active_assignments_by_card(card_id, site_location, device, today)
    if not assignments:
        return False, "Guard is not actively deployed at this site today.", None
    if len(assignments) > 1:
        return False, "This RFID card matches multiple deployed guards today. Check the guard deployment selection.", None

    assignment = assignments[0]
    guard = assignment.guard
    deployment = assignment.deployment

    if contract_guard_limit_exceeded_for_deployment(deployment, today):
        return False, "you have excedd contacted number of guards", None

    if action in ["mark_absent", "absent", "replace"]:
        replacement_guard = Guard.objects.filter(guard_id=replacement_guard_id, status="Active").first()
        if not replacement_guard:
            return False, "Select an active replacement guard.", None

        if replacement_guard == guard:
            return False, "Replacement guard cannot be the same as the absent assigned guard.", None

        attendance, _ = Attendance.objects.get_or_create(
            guard=guard,
            attendance_date=today,
            defaults={"status": "Present"},
        )
        attendance.status = "Absent"
        attendance.replacement_guard = replacement_guard
        attendance.absence_reason = "Assigned guard absent - replaced via IoT"
        attendance.check_in_time = attendance.check_in_time or current_time
        attendance.check_out_time = attendance.check_out_time or current_time
        attendance.save()
        create_attendance_swipe(attendance, device, "replacement")
        return True, f"{guard.full_name} marked absent. Replacement recorded for {replacement_guard.full_name}.", attendance

    attendance, created = Attendance.objects.get_or_create(
        guard=guard,
        attendance_date=today,
        defaults={
            "status": "Present",
            "check_in_time": current_time,
        },
    )

    current_swipe_count = attendance.swipes.count()
    if current_swipe_count >= HOURLY_SWIPE_COUNT and not attendance.check_out_time:
        close_attendance_from_existing_swipes(attendance, guard, today, site_location, device)
        return True, f"12th swipe captured as check-out for {guard.full_name}. Attendance counted and salary updated.", attendance

    if action == "check_out":
        if current_swipe_count >= HOURLY_SWIPE_COUNT:
            return False, f"{guard.full_name} already has the maximum 12 swipes today.", attendance
        create_attendance_swipe(attendance, device, "check_out")
        close_attendance_for_checkout(attendance, guard, today, site_location, device)
        return True, f"Check-out recorded for {guard.full_name}. Attendance is now ready for payroll.", attendance

    if attendance.check_out_time:
        return False, f"{guard.full_name} has already checked out today.", attendance

    if created or not attendance.check_in_time:
        attendance.check_in_time = current_time
        attendance.status = "Present"
        attendance.save()
        create_attendance_swipe(attendance, device, "check_in")
        update_deployment_after_iot_swipe(guard, today, site_location, GUARD_DEPLOYMENT_STATUS_ON_SITE, current_time)
        return True, f"Check-in recorded for {guard.full_name}. Hourly swipes are now expected.", attendance

    next_swipe_number = attendance.swipes.count() + 1
    if next_swipe_number > HOURLY_SWIPE_COUNT:
        return False, f"{guard.full_name} already has the maximum 12 swipes today.", attendance

    if next_swipe_number == HOURLY_SWIPE_COUNT:
        create_attendance_swipe(attendance, device, "check_out")
        close_attendance_for_checkout(attendance, guard, today, site_location, device)
        return True, f"12th swipe captured as check-out for {guard.full_name}. Attendance counted and salary updated.", attendance

    create_attendance_swipe(attendance, device, "hourly")
    update_deployment_after_iot_swipe(guard, today, site_location, GUARD_DEPLOYMENT_STATUS_ON_SITE, current_time)
    return True, f"Hourly swipe {next_swipe_number} of {HOURLY_SWIPE_COUNT} recorded for {guard.full_name}.", attendance

def replacement_guard_is_available(replacement_guard, attendance):
    if not replacement_guard or not attendance:
        return True

    if replacement_guard == attendance.guard:
        return False

    target_shift = Shift.objects.filter(
        guard=attendance.guard,
        start_time=attendance.attendance_date,
    ).first()
    replacement_shift = Shift.objects.filter(
        guard=replacement_guard,
        start_time=attendance.attendance_date,
    ).first()
    target_shift_code = get_shift_code(target_shift)
    replacement_shift_code = get_shift_code(replacement_shift)

    if target_shift_code == "D/N":
        return replacement_shift is None or replacement_shift_code == "D/N"

    already_scheduled = Attendance.objects.filter(
        guard=replacement_guard,
        attendance_date=attendance.attendance_date,
        status__in=["Present", "Late"],
    ).exclude(attendance_id=attendance.attendance_id).exists()
    already_replacing = Attendance.objects.filter(
        replacement_guard=replacement_guard,
        attendance_date=attendance.attendance_date,
    ).exclude(attendance_id=attendance.attendance_id).exists()

    return not already_scheduled and not already_replacing


def get_shift_code(shift):
    if not shift:
        return "D"

    shift_type = str(shift.shift_type or "").strip().upper()
    shift_code_map = {
        "DAY": "D",
        "D": "D",
        "O": "D",
        "NIGHT": "N",
        "N": "N",
        "WEEKEND": "D/N",
        "DAY/NIGHT": "D/N",
        "D/N": "D/N",
        "DN": "D/N",
        "PUBLIC HOLIDAY": "PH",
        "PUBLIC_HOLIDAY": "PH",
        "HOLIDAY": "PH",
        "PH": "PH",
    }

    if shift_type in shift_code_map:
        return shift_code_map[shift_type]

    shift_name = getattr(shift, "shift_name", "")
    if shift_name:
        code_match = re.search(r"\(([A-Z]+)\)\s*$", shift_name.strip(), re.IGNORECASE)
        if code_match:
            return shift_code_map.get(code_match.group(1).upper(), code_match.group(1).upper())

    return "D"



def get_attendance_shift(attendance, deployment=None):
    assignment_filters = {
        "guard": attendance.guard,
        "deployment_date": attendance.attendance_date,
    }
    if deployment:
        assignment_filters["deployment"] = deployment

    assignment = DeploymentGuard.objects.filter(**assignment_filters).order_by("-deployment_guard_id").first()
    if assignment:
        return assignment

    shift = Shift.objects.filter(
        guard=attendance.guard,
        start_time=attendance.attendance_date,
    ).first()
    if shift:
        return shift
    if deployment and deployment.shift_id:
        return deployment.shift
    attendance_time = attendance.check_in_time or attendance.check_out_time
    if attendance_time:
        inferred_shift_type = "N" if attendance_time >= time(18, 0) else "D"
        return SimpleNamespace(shift_type=inferred_shift_type, shift_name=f"Inferred Shift ({inferred_shift_type})")
    return None

def normalize_header(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def normalize_shift_type(value):
    shift_type = str(value or "").strip().lower()

    if shift_type in ["n/d", "nd", "night/day", "night_day"]:
        return None

    if shift_type in ["n", "night", "night_shift"]:
        return "N"

    if shift_type in ["d/n", "dn", "day/night", "day_night", "weekend", "w"]:
        return "D/N"

    if shift_type in ["ph", "public_holiday", "public holiday", "holiday"]:
        return "PH"

    return "D"


def normalize_attendance_status(value):
    attendance_status = str(value or "").strip().lower()

    if attendance_status in ["absent", "a", "no", "n", "false", "0"]:
        return "Absent"

    if attendance_status in ["late", "l"]:
        return "Late"

    if attendance_status in ["on_leave", "on leave", "leave"]:
        return "On Leave"

    return "Present"


def parse_roster_date(value):
    if not value:
        return None

    if hasattr(value, "date"):
        return value.date()

    text = str(value).strip()
    parsed_date = parse_date(text)
    if parsed_date:
        return parsed_date

    for date_format in ["%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"]:
        try:
            return timezone.datetime.strptime(text, date_format).date()
        except ValueError:
            pass

    return None


def parse_excel_serial_date(value):
    try:
        serial = float(value)
    except (TypeError, ValueError):
        return parse_roster_date(value)

    # Excel's Windows date system starts at 1899-12-30 when adjusted for leap-year compatibility.
    return timezone.datetime(1899, 12, 30).date() + timedelta(days=int(serial))


def excel_column_index(cell_reference):
    letters = re.sub(r"[^A-Z]", "", str(cell_reference or "").upper())
    column_index = 0

    for letter in letters:
        column_index = column_index * 26 + (ord(letter) - ord("A") + 1)

    return max(column_index - 1, 0)


def read_csv_rows(uploaded_file):
    text = uploaded_file.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def read_xlsx_sheet_rows(uploaded_file):
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }

    with zipfile.ZipFile(uploaded_file) as workbook:
        shared_strings = []

        if "xl/sharedStrings.xml" in workbook.namelist():
            shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("main:si", ns):
                shared_strings.append("".join(text.text or "" for text in item.findall(".//main:t", ns)))

        workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
        first_sheet = workbook_root.find("main:sheets/main:sheet", ns)
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")

        rels_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        sheet_target = None
        for rel in rels_root.findall("pkgrel:Relationship", ns):
            if rel.attrib.get("Id") == rel_id:
                sheet_target = rel.attrib.get("Target")
                break

        if not sheet_target:
            return []

        sheet_path = "xl/" + sheet_target.lstrip("/")
        sheet_root = ET.fromstring(workbook.read(sheet_path))
        rows = []

        for row in sheet_root.findall(".//main:sheetData/main:row", ns):
            values = []
            for cell in row.findall("main:c", ns):
                cell_index = excel_column_index(cell.attrib.get("r"))
                while len(values) < cell_index:
                    values.append("")

                value_node = cell.find("main:v", ns)
                value = value_node.text if value_node is not None else ""

                if cell.attrib.get("t") == "s" and value != "":
                    value = shared_strings[int(value)]

                if cell.attrib.get("t") == "inlineStr":
                    value = "".join(text.text or "" for text in cell.findall(".//main:t", ns))

                values.append(value)

            if any(str(value).strip() for value in values):
                rows.append(values)

        return rows


def rows_to_dicts(rows):
    if not rows:
        return []

    headers = [normalize_header(value) for value in rows[0]]
    return [
        {headers[index]: value for index, value in enumerate(row) if index < len(headers)}
        for row in rows[1:]
    ]


def read_xlsx_rows(uploaded_file):
    return rows_to_dicts(read_xlsx_sheet_rows(uploaded_file))


def read_roster_rows(uploaded_file):
    filename = uploaded_file.name.lower()

    if filename.endswith(".csv"):
        return read_csv_rows(uploaded_file)

    if filename.endswith(".xlsx"):
        return read_xlsx_rows(uploaded_file)

    raise ValueError("Upload a CSV or XLSX roster file.")


def read_roster_sheet(uploaded_file):
    filename = uploaded_file.name.lower()

    if filename.endswith(".csv"):
        text = uploaded_file.read().decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    if filename.endswith(".xlsx"):
        return read_xlsx_sheet_rows(uploaded_file)

    raise ValueError("Upload a CSV or XLSX roster file.")


def get_row_value(row, *names):
    for name in names:
        value = row.get(name)
        if value not in [None, ""]:
            return value
    return ""


def find_guard_for_roster_row(row):
    employee_number = str(get_row_value(
        row,
        "employee_number",
        "employee_no",
        "emp_no",
        "emp_number",
        "pers_no",
        "personnel_no",
        "person_no",
            "guard_number",
    )).strip()
    guard_name = str(get_row_value(row, "name", "guard", "guard_name", "full_name")).strip()

    if employee_number:
        guard = Guard.objects.filter(guard_number__iexact=employee_number).first()
        if guard:
            return guard

    if guard_name:
        return Guard.objects.filter(full_name__iexact=guard_name).first()

    return None


def find_client_for_roster_row(row):
    client_name = str(get_row_value(row, "client", "client_name", "deployment_area")).strip()

    if client_name:
        return Client.objects.filter(client_name__iexact=client_name).first()

    return None


def schedule_month_bounds(work_date):
    return (
        date(work_date.year, work_date.month, 1),
        date(work_date.year, work_date.month, monthrange(work_date.year, work_date.month)[1]),
    )


def parse_schedule_period(rows):
    for row in rows:
        row_text = " ".join(str(value or "").strip() for value in row if str(value or "").strip())
        date_values = re.findall(r"\d{4}-\d{2}-\d{2}", row_text)

        if "scheduled period" in row_text.lower() and len(date_values) >= 2:
            return parse_date(date_values[0]), parse_date(date_values[1])

    return None, None


def is_wide_schedule_header(row):
    headers = [normalize_header(value) for value in row]
    has_person = any(header in headers for header in ["pers_no", "personnel_no", "person_no", "employee_number"])
    has_name = "name" in headers or "guard_name" in headers
    date_like_columns = sum(1 for value in row if re.match(r"^[A-Za-z]{2}/\d{1,2}$", str(value or "").strip()))

    return has_person and has_name and date_like_columns >= 3


def expand_wide_roster_rows(rows):
    period_start, _ = parse_schedule_period(rows)
    header_index = None

    for index, row in enumerate(rows):
        if is_wide_schedule_header(row):
            header_index = index
            break

    if header_index is None or not period_start:
        return []

    header = rows[header_index]
    normalized_headers = [normalize_header(value) for value in header]
    expanded_rows = []

    date_columns = [
        (index, period_start + timedelta(days=offset))
        for offset, index in enumerate(
            column_index
            for column_index, value in enumerate(header)
            if re.match(r"^[A-Za-z]{2}/\d{1,2}$", str(value or "").strip())
        )
    ]

    for row in rows[header_index + 1:]:
        if not any(str(value or "").strip() for value in row):
            continue

        row_values = {
            normalized_headers[index]: value
            for index, value in enumerate(row)
            if index < len(normalized_headers) and normalized_headers[index]
        }

        if not get_row_value(row_values, "name", "guard_name", "full_name"):
            continue

        for column_index, shift_date in date_columns:
            shift_code = str(row[column_index] if column_index < len(row) else "").strip()

            if not shift_code or shift_code in ["-", "_"]:
                continue

            roster_row = dict(row_values)
            roster_row.update({
                "shift_date": shift_date.isoformat(),
                "shift_type": shift_code,
                "shift_name": f"Imported Schedule ({shift_code.upper()})",
                "attendance_status": "Present",
            })
            expanded_rows.append(roster_row)

    return expanded_rows


def is_monthly_schedule_header(row):
    headers = [normalize_header(value) for value in row]
    has_guard_column = any(value in headers for value in ["guard", "guard_number", "employee_number", "name", "guard_name", "full_name"])
    has_client_column = any(value in headers for value in ["client", "client_name"])
    has_location_column = any(value in headers for value in ["location", "site", "site_location"])
    has_basic_columns = has_guard_column and has_client_column and has_location_column
    day_columns = sum(1 for value in row if str(value or "").strip().isdigit())
    return has_basic_columns and day_columns >= 7


def expand_monthly_schedule_rows(rows, year, month):
    header_index = None

    for index, row in enumerate(rows):
        if is_monthly_schedule_header(row):
            header_index = index
            break

    if header_index is None:
        return []

    last_day = monthrange(year, month)[1]
    header = rows[header_index]
    normalized_headers = [normalize_header(value) for value in header]
    day_columns = []

    for column_index, value in enumerate(header):
        text = str(value or "").strip()
        if not text.isdigit():
            continue

        day_number = int(text)
        if 1 <= day_number <= last_day:
            day_columns.append((column_index, date(year, month, day_number)))

    expanded_rows = []

    for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not any(str(value or "").strip() for value in row):
            continue

        row_values = {
            normalized_headers[index]: value
            for index, value in enumerate(row)
            if index < len(normalized_headers) and normalized_headers[index]
        }

        guard_name = str(get_row_value(row_values, "guard", "name", "guard_name", "full_name")).strip()
        if not guard_name:
            continue

        for column_index, shift_date in day_columns:
            shift_code = str(row[column_index] if column_index < len(row) else "").strip().upper()

            if not shift_code or shift_code in ["-", "_", "O", "OFF", "REST", "R"]:
                continue

            roster_row = dict(row_values)
            roster_row.update({
                "row_number": row_number,
                "shift_date": shift_date.isoformat(),
                "shift_type": shift_code,
                "shift_name": f"Scheduled Duty ({shift_code})",
                "attendance_status": "Present",
            })
            expanded_rows.append(roster_row)

    return expanded_rows


def clean_schedule_period_value(value, fallback):
    try:
        return int(value or fallback)
    except (TypeError, ValueError):
        return fallback


@login_required
def shift_import(request):
    if request.method == "POST":
        roster_file = request.FILES.get("roster_file")
        schedule_year = clean_schedule_period_value(request.POST.get("schedule_year"), timezone.localdate().year)
        schedule_month = clean_schedule_period_value(request.POST.get("schedule_month"), timezone.localdate().month)
        required_guards = clean_schedule_period_value(request.POST.get("required_guards"), 1)
        if schedule_month < 1 or schedule_month > 12:
            schedule_month = timezone.localdate().month
        if required_guards < 1:
            required_guards = 1

        if not roster_file:
            messages.error(request, "Choose a roster file to import.")
            return redirect("shift_import")

        try:
            sheet_rows = read_roster_sheet(roster_file)
        except (ValueError, zipfile.BadZipFile, ET.ParseError) as error:
            messages.error(request, str(error))
            return redirect("shift_import")

        rows = (
            expand_monthly_schedule_rows(sheet_rows, schedule_year, schedule_month)
            or expand_wide_roster_rows(sheet_rows)
            or rows_to_dicts(sheet_rows)
        )
        created_count = 0
        updated_count = 0
        deployment_created_count = 0
        deployment_existing_count = 0
        deployment_assignment_created_count = 0
        capacity_skipped_count = 0
        skipped_rows = []

        for row_number, row in enumerate(rows, start=2):
            normalized_row = {normalize_header(key): value for key, value in row.items()}
            shift_date_value = get_row_value(normalized_row, "shift_date", "date", "date_scheduled", "start_date")
            shift_type_value = get_row_value(normalized_row, "shift_type", "shift", "d_n", "d_or_n")
            shift_name = str(get_row_value(normalized_row, "shift_name", "name") or "Imported Shift").strip()
            site_location = str(get_row_value(normalized_row, "site_location", "site", "location")).strip()
            client_name = str(get_row_value(normalized_row, "client", "client_name")).strip()

            guard = find_guard_for_roster_row(normalized_row)
            client = find_client_for_roster_row(normalized_row)
            shift_date = parse_excel_serial_date(shift_date_value)
            raw_shift_code = str(shift_type_value or "").strip().upper()

            if raw_shift_code in ["", "-", "_", "O", "OFF", "REST", "R"]:
                continue

            if raw_shift_code in ["N/D", "ND", "NIGHT/DAY", "NIGHT_DAY"]:
                skipped_rows.append(normalized_row.get("row_number", row_number))
                continue

            if not guard or not shift_date:
                skipped_rows.append(normalized_row.get("row_number", row_number))
                continue

            deployment = get_matching_deployment(guard, shift_date, site_location, client_name)
            if not deployment:
                if not client or not site_location:
                    skipped_rows.append(normalized_row.get("row_number", row_number))
                    continue

                deployment_start, deployment_end = schedule_month_bounds(shift_date)
                deployment, deployment_created = get_or_create_deployment_for_schedule(
                    guard,
                    client,
                    site_location,
                    deployment_start,
                    deployment_end,
                )

                if deployment_created:
                    deployment_created_count += 1
                else:
                    deployment_existing_count += 1
            else:
                deployment_existing_count += 1

            shift_type = normalize_shift_type(shift_type_value)
            if not shift_type:
                skipped_rows.append(normalized_row.get("row_number", row_number))
                continue

            existing_count = scheduled_guard_count_for_site(
                deployment.client,
                deployment.site_location,
                shift_date,
                exclude_guard_ids=[guard.guard_id],
            )
            existing_guard_shift = Shift.objects.filter(guard=guard, start_time=shift_date).exists()
            if not existing_guard_shift and existing_count >= required_guards:
                capacity_skipped_count += 1
                continue

            _, created = Shift.objects.update_or_create(
                guard=guard,
                start_time=shift_date,
                defaults={
                    "shift_name": shift_name,
                    "end_time": shift_date,
                    "shift_type": shift_type,
                },
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

            _, assignment_created = ensure_guard_deployment_for_date(deployment, guard, shift_date)
            if assignment_created:
                deployment_assignment_created_count += 1
        if created_count:
            messages.success(request, f"Imported {created_count} scheduled duty shift(s).")
        else:
            messages.info(request, "No new scheduled duty shifts were imported.")

        if updated_count:
            messages.info(request, f"Updated {updated_count} existing scheduled duty shift(s).")

        if deployment_created_count:
            messages.success(request, f"Created {deployment_created_count} scheduled guard deployment(s) from the imported roster.")
        elif deployment_existing_count:
            messages.info(request, "Imported schedule rows were linked to existing scheduled guard deployments.")

        if deployment_assignment_created_count:
            messages.success(request, f"Created {deployment_assignment_created_count} daily guard deployment assignment(s) for attendance swipes.")
        if skipped_rows:
            messages.warning(request, f"Skipped row(s): {', '.join(str(row) for row in skipped_rows)} because they did not match an existing guard/client/site.")

        if capacity_skipped_count:
            messages.warning(request, f"Skipped {capacity_skipped_count} schedule row(s) because they exceeded {required_guards} required guard(s) for the site.")

        return redirect("shift_list")

    return render(request, "guardmanagementsystem/shift_import.html", {
        "current_month": timezone.localdate().month,
        "current_year": timezone.localdate().year,
        "required_guards": 1,
    })


def excel_cell_ref(row_number, column_number):
    letters = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row_number}"


def xml_escape(value):
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def xlsx_cell(row_number, column_number, value="", style_id=0):
    cell_ref = excel_cell_ref(row_number, column_number)
    style_attr = f' s="{style_id}"' if style_id else ""

    if value in [None, ""]:
        return f'<c r="{cell_ref}"{style_attr}/>'

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return f'<c r="{cell_ref}"{style_attr}><v>{value}</v></c>'

    return f'<c r="{cell_ref}" t="inlineStr"{style_attr}><is><t>{xml_escape(value)}</t></is></c>'


def xlsx_row(row_number, values, style_ids=None):
    style_ids = style_ids or []
    cells = [
        xlsx_cell(row_number, index + 1, value, style_ids[index] if index < len(style_ids) else 0)
        for index, value in enumerate(values)
    ]
    return f'<row r="{row_number}">{"".join(cells)}</row>'


def xlsx_merge(ref):
    return f'<mergeCell ref="{ref}"/>'


def build_roster_rows(schedule_year, schedule_month, contract_id=None):
    first_day = date(schedule_year, schedule_month, 1)
    last_day = date(schedule_year, schedule_month, monthrange(schedule_year, schedule_month)[1])
    assignments = DeploymentGuard.objects.select_related(
        "deployment",
        "deployment__client",
        "deployment__contract",
        "deployment__contract__iot_device",
        "guard",
    ).filter(
        deployment_date__gte=first_day,
        deployment_date__lte=last_day,
    )

    if contract_id:
        assignments = assignments.filter(deployment__contract_id=contract_id)

    shifts = Shift.objects.filter(
        start_time__gte=first_day,
        start_time__lte=last_day,
    )
    shift_by_guard_date = {
        (shift.guard_id, shift.start_time): get_shift_code(shift)
        for shift in shifts
    }

    grouped = {}
    for assignment in assignments.order_by(
        "deployment__contract__contract_number",
        "deployment__client__client_name",
        "deployment__site_location",
        "guard__guard_number",
        "deployment_date",
    ):
        deployment = assignment.deployment
        contract = deployment.contract
        key = (contract.contract_id if contract else None, deployment.deployment_id, assignment.guard_id)
        row = grouped.setdefault(key, {
            "contract": contract,
            "deployment": deployment,
            "guard": assignment.guard,
            "days": {},
        })
        row["days"][assignment.deployment_date.day] = shift_by_guard_date.get(
            (assignment.guard_id, assignment.deployment_date),
            "D",
        )

    return list(grouped.values())


def roster_default_contract(contract_id=None):
    contracts = Contract.objects.select_related("client", "iot_device").order_by("contract_number")
    if contract_id:
        contract = contracts.filter(pk=contract_id).first()
        if contract:
            return contract
    return contracts.first()


def deployment_iot_device_code(deployment, contract=None):
    if contract and contract.iot_device:
        return contract.iot_device.device_code
    return ""


def build_roster_xlsx(schedule_year, schedule_month, contract_id=None):
    last_day = monthrange(schedule_year, schedule_month)[1]
    day_numbers = list(range(1, last_day + 1))
    roster_rows = build_roster_rows(schedule_year, schedule_month, contract_id)
    default_contract = roster_default_contract(contract_id)
    default_device = default_contract.iot_device.device_code if default_contract and default_contract.iot_device else ""
    month_label = date(schedule_year, schedule_month, 1).strftime("%B %Y")
    header_columns = [
        "Contract Number",
        "Client Name",
        "Site Location",
        "IoT Device Code",
        "Guard Number",
        "Guard Name",
        "Shift Type",
        *day_numbers,
    ]

    rows_xml = []
    merges = [xlsx_merge(f"A1:{excel_cell_ref(1, len(header_columns))}")]
    rows_xml.append(xlsx_row(1, ["INTEGRATED GUARD DUTY ROSTER"], [1]))
    rows_xml.append(xlsx_row(2, [
        "Roster Month",
        month_label,
        "Default Contract",
        default_contract.contract_number if default_contract else "",
        "Default Device",
        default_device,
    ], [2, 3, 2, 3, 2, 3]))
    rows_xml.append(xlsx_row(3, [""] * len(header_columns)))
    rows_xml.append(xlsx_row(4, header_columns, [4] * len(header_columns)))

    current_row = 5
    for roster in roster_rows:
        contract = roster["contract"]
        deployment = roster["deployment"]
        guard = roster["guard"]
        day_values = [roster["days"].get(day, "O") for day in day_numbers]
        active_codes = [code for code in day_values if code and code != "O"]
        shift_type = active_codes[0] if active_codes else ""
        values = [
            contract.contract_number if contract else "",
            deployment.client.client_name if deployment and deployment.client else "",
            deployment.site_location if deployment else "",
            deployment_iot_device_code(deployment, contract),
            guard.guard_number or "",
            guard.full_name,
            shift_type,
            *day_values,
        ]
        style_ids = [5] * 7 + [7 if value == "O" else 6 for value in day_values]
        rows_xml.append(xlsx_row(current_row, values, style_ids))
        current_row += 1

    min_blank_rows = 34
    while current_row < 5 + max(min_blank_rows, len(roster_rows)):
        rows_xml.append(xlsx_row(current_row, [""] * len(header_columns), [8 if current_row % 2 else 0] * len(header_columns)))
        current_row += 1

    col_widths = [18, 22, 24, 30, 16, 24, 14] + [5] * last_day
    cols_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(col_widths, start=1)
    )
    dimension = f"A1:{excel_cell_ref(current_row - 1, len(header_columns))}"
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <dimension ref="{dimension}"/>
    <sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
    <cols>{cols_xml}</cols>
    <sheetData>{''.join(rows_xml)}</sheetData>
    <mergeCells count="{len(merges)}">{''.join(merges)}</mergeCells>
</worksheet>'''

    workbook_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheets><sheet name="Schedule Upload" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
    <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
    <fonts count="4"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
    <fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD9EAF7"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF305496"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFE2F0D9"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFCE4D6"/><bgColor indexed="64"/></patternFill></fill></fills>
    <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FF9CC2E5"/></left><right style="thin"><color rgb="FF9CC2E5"/></right><top style="thin"><color rgb="FF9CC2E5"/></top><bottom style="thin"><color rgb="FF9CC2E5"/></bottom><diagonal/></border></borders>
    <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
    <cellXfs count="9"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFill="1"/><xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1"/><xf numFmtId="0" fontId="2" fillId="5" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf><xf numFmtId="0" fontId="2" fillId="6" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf><xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1"/></cellXfs>
</styleSheet>'''

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types)
        xlsx.writestr("_rels/.rels", root_rels)
        xlsx.writestr("xl/workbook.xml", workbook_xml)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        xlsx.writestr("xl/styles.xml", styles_xml)
        xlsx.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    output.seek(0)
    return output.getvalue()


@login_required
def schedule_xlsx_template(request):
    today = timezone.localdate()
    schedule_year = clean_schedule_period_value(request.GET.get("year"), today.year)
    schedule_month = clean_schedule_period_value(request.GET.get("month"), today.month)
    contract_id = request.GET.get("contract") or None
    if schedule_month < 1 or schedule_month > 12:
        schedule_month = today.month

    workbook = build_roster_xlsx(schedule_year, schedule_month, contract_id)
    filename = f"integrated_guard_duty_roster_{schedule_year}_{schedule_month:02d}.xlsx"
    response = HttpResponse(
        workbook,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@login_required
def schedule_csv_template(request):
    today = timezone.localdate()
    schedule_year = clean_schedule_period_value(request.GET.get("year"), today.year)
    schedule_month = clean_schedule_period_value(request.GET.get("month"), today.month)
    if schedule_month < 1 or schedule_month > 12:
        schedule_month = today.month

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="scheduled_guard_template.csv"'

    writer = csv.writer(response)
    day_numbers = list(range(1, monthrange(schedule_year, schedule_month)[1] + 1))
    writer.writerow(["Guard", "Client", "Location", "Shift type", *day_numbers])
    writer.writerow(["John Doe", "ABC Ltd", "Main Gate", "D", *["D" if day % 7 else "O" for day in day_numbers]])

    return response


@login_required
def dashboard(request):
    current_year = timezone.now().year
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    incident_counts = dict(
        Incident.objects.filter(incident_date__year=current_year)
        .annotate(month=ExtractMonth("incident_date"))
        .values("month")
        .annotate(total=Count("incident_id"))
        .values_list("month", "total")
    )
    total_guards = Guard.objects.count()
    total_clients = Client.objects.count()
    total_deployments = Deployment.objects.count()
    total_assets = Asset.objects.count()
    total_shifts = Shift.objects.count()
    total_salaries = Salary.objects.count()
    total_incidents = Incident.objects.count()

    context = {
        "total_guards": total_guards,
        "total_clients": total_clients,
        "total_deployments": total_deployments,
        "total_assets": total_assets,
        "total_shifts": total_shifts,
        "total_salaries": total_salaries,
        "total_incidents": total_incidents,
        # Chart data
        "chart_labels": [
            "Guards",
            "Clients",
            "Deployments",
            "Assets",
            "Shifts",
            "Salaries",
            "Incidents",
        ],

        "chart_values": [
            total_guards,
            total_clients,
            total_deployments,
            total_assets,
            total_shifts,
            total_salaries,
            total_incidents,
        ],
        "month_labels": month_labels,
        "monthly_incidents": [incident_counts.get(month, 0) for month in range(1, 13)],
    }

    return render(request, "guardmanagementsystem/dashboard.html", context)


@login_required
def audit_log_list(request):
    logs = AuditLog.objects.select_related("user").order_by("-created_at")[:200]
    return render(request, "guardmanagementsystem/audit_log_list.html", {
        "logs": logs
    })


@login_required
def user_list(request):
    users = User.objects.order_by("username")
    return render(request, "guardmanagementsystem/user_list.html", {
        "users": users
    })


@login_required
def user_add(request):
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_action(request, "CREATE", f"Created user account {user.username}.")
            messages.success(request, "User account added successfully.")
            return redirect("user_list")
    else:
        form = UserCreateForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add User",
        "button_text": "Create User",
    })


@login_required
def user_edit(request, id):
    user = get_object_or_404(User, pk=id)

    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            updated_user = form.save()
            log_action(request, "UPDATE", f"Updated user account {updated_user.username}.")
            messages.success(request, "User account updated successfully.")
            return redirect("user_list")
    else:
        form = UserEditForm(instance=user)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit User",
    })


@login_required
def user_delete(request, id):
    user = get_object_or_404(User, pk=id)

    if user == request.user:
        messages.error(request, "You cannot delete the account you are currently using.")
        return redirect("user_list")

    if request.method == "POST":
        username = user.username
        user.delete()
        log_action(request, "DELETE", f"Deleted user account {username}.")
        messages.success(request, "User account deleted successfully.")
        return redirect("user_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": user,
        "title": "Delete User"
    })

def show_records(request, queryset, template_name, context_name):
    return render(request, template_name, {
        context_name: queryset
    })


def add_record(
    request,
    form_class,
    title,
    redirect_name,
    success_message,
    template_name="guardmanagementsystem/form.html",
    extra_context=None
):
    if request.method == "POST":
        form = form_class(request.POST)

        if form.is_valid():
            record = form.save()
            log_action(request, "CREATE", f"Created {record}.")
            messages.success(request, success_message)
            return redirect(redirect_name)
    else:
        form = form_class()

    context = {
        "form": form,
        "title": title
    }
    if extra_context:
        context.update(extra_context)

    return render(request, template_name, context)


def edit_record(
    request,
    model_class,
    form_class,
    lookup,
    title,
    redirect_name,
    success_message,
    template_name="guardmanagementsystem/form.html",
    extra_context=None
):
    record = get_object_or_404(model_class, **lookup)

    if request.method == "POST":
        form = form_class(request.POST, instance=record)

        if form.is_valid():
            record = form.save()
            log_action(request, "UPDATE", f"Updated {record}.")
            messages.success(request, success_message)
            return redirect(redirect_name)
    else:
        form = form_class(instance=record)

    context = {
        "form": form,
        "title": title
    }
    if extra_context:
        context.update(extra_context)

    return render(request, template_name, context)


def delete_record(request, model_class, lookup, title, redirect_name, success_message):
    record = get_object_or_404(model_class, **lookup)

    if request.method == "POST":
        description = f"Deleted {record}."
        record.delete()
        log_action(request, "DELETE", description)
        messages.success(request, success_message)
        return redirect(redirect_name)

    return render(request, "guardmanagementsystem/delete.html", {
        "object": record,
        "title": title
    })



@login_required
def guard_list(request):
    return show_records(request, Guard.objects.all(), "guardmanagementsystem/guard_list.html", "guards")


@login_required
def guard_add(request):
    return add_record(request, GuardForm, "Add Guard", "guard_list", "Guard added successfully")


@login_required
def guard_edit(request, id):
    return edit_record(request, Guard, GuardForm, {"guard_id": id}, "Edit Guard", "guard_list", "Guard updated successfully")


@login_required
def guard_delete(request, id):
    return delete_record(request, Guard, {"guard_id": id}, "Delete Guard", "guard_list", "Guard deleted successfully")


@login_required
def rfid_card_list(request):
    return show_records(request, RFIDCard.objects.all(), "guardmanagementsystem/rfid_card_list.html", "rfid_cards")


@login_required
def rfid_card_add(request):
    return add_record(request, RFIDCardForm, "Add RFID Card", "rfid_card_list", "RFID card added successfully")


@login_required
def rfid_card_edit(request, id):
    return edit_record(request, RFIDCard, RFIDCardForm, {"rfid_card_id": id}, "Edit RFID Card", "rfid_card_list", "RFID card updated successfully")


@login_required
def rfid_card_delete(request, id):
    return delete_record(request, RFIDCard, {"rfid_card_id": id}, "Delete RFID Card", "rfid_card_list", "RFID card deleted successfully")


@login_required
def rfid_card_deactivate(request, id):
    card = get_object_or_404(RFIDCard, rfid_card_id=id)
    card.status = "Inactive"
    card.save(update_fields=["status"])
    log_action(request, "UPDATE", f"Remotely deactivated RFID card {card.card_number}.")
    messages.success(request, f"RFID card {card.card_number} deactivated remotely.")
    return redirect("rfid_card_list")


@login_required
def rfid_card_activate(request, id):
    card = get_object_or_404(RFIDCard, rfid_card_id=id)
    inactive_assignment = None

    if inactive_assignment:
        messages.error(request, "Cannot activate this RFID card because one assigned deployed guard is not active.")
        return redirect("rfid_card_list")

    card.status = "Active"
    card.save(update_fields=["status"])
    log_action(request, "UPDATE", f"Remotely activated RFID card {card.card_number}.")
    messages.success(request, f"RFID card {card.card_number} activated.")
    return redirect("rfid_card_list")


@login_required
def client_list(request):
    return show_records(request, Client.objects.all(), "guardmanagementsystem/client_list.html", "clients")


@login_required
def client_add(request):
    return add_record(request, ClientForm, "Add Client", "client_list", "Client added successfully")


@login_required
def client_edit(request, id):
    return edit_record(request, Client, ClientForm, {"client_id": id}, "Edit Client", "client_list", "Client updated successfully")


@login_required
def client_delete(request, id):
    return delete_record(request, Client, {"client_id": id}, "Delete Client", "client_list", "Client deleted successfully")


@login_required
def contract_list(request):
    contracts = Contract.objects.select_related("client", "iot_device").order_by("-start_date", "contract_number")
    return show_records(request, contracts, "guardmanagementsystem/contract_list.html", "contracts")


@login_required
def contract_iot_devices(request):
    selected_id = request.GET.get("selected_id")
    unavailable_device_ids = Contract.objects.filter(
        iot_device__isnull=False,
        status__in=["Draft", "Active"],
    )
    if selected_id:
        unavailable_device_ids = unavailable_device_ids.exclude(iot_device_id=selected_id)
    unavailable_device_ids = unavailable_device_ids.values_list("iot_device_id", flat=True)

    devices = IoTDevice.objects.filter(is_active=True).exclude(pk__in=unavailable_device_ids)

    if selected_id:
        devices = IoTDevice.objects.filter(
            Q(pk=selected_id) | Q(pk__in=devices.values("pk"))
        )

    payload = []
    for device in devices.order_by("device_number"):
        payload.append({
            "id": device.device_id,
            "label": f"{device.device_number} ({device.device_code})",
            "site_location": "",
        })

    return JsonResponse({"devices": payload})


@login_required
def contract_add(request):
    return add_record(request, ContractForm, "Add Contract", "contract_list", "Contract added successfully.")


@login_required
def contract_edit(request, id):
    return edit_record(request, Contract, ContractForm, {"contract_id": id}, "Edit Contract", "contract_list", "Contract updated successfully.")


@login_required
def contract_delete(request, id):
    return delete_record(request, Contract, {"contract_id": id}, "Delete Contract", "contract_list", "Contract deleted successfully.")



def guard_shift_label(guard):
    return f"{guard.guard_number or '-'} - {guard.full_name}"


def add_guard_to_shift_bucket(bucket, guard):
    label = guard_shift_label(guard)
    if label not in bucket:
        bucket.append(label)


def decorate_deployment_shift_distribution(deployment):
    contract = deployment.contract
    deployment.required_guard_count = contract.number_of_guards if contract else None
    deployment.day_shift_count = contract.day_shift_guards if contract else None
    deployment.night_shift_count = contract.night_shift_guards if contract else None
    return deployment

@login_required
def deployment_list(request):
    all_deployments = list(
        Deployment.objects.select_related("client", "contract", "shift")
        .prefetch_related("deployment_guards__guard")
        .order_by("-start_date", "-deployment_id")
    )
    status_updates = []
    today = timezone.localdate()
    for deployment in all_deployments:
        old_status = deployment.status
        deployment.sync_date_status(today)
        if deployment.status != old_status:
            status_updates.append(deployment)

    if status_updates:
        Deployment.objects.bulk_update(status_updates, ["status"])

    deployments = []
    seen_contract_ids = set()
    for deployment in all_deployments:
        if deployment.contract_id:
            if deployment.contract_id in seen_contract_ids:
                continue
            seen_contract_ids.add(deployment.contract_id)

        decorate_deployment_shift_distribution(deployment)
        deployments.append(deployment)

    return show_records(request, deployments, "guardmanagementsystem/deployment_list.html", "deployments")


@login_required
def deployment_add(request):
    return add_record(request, DeploymentForm, "Add Deployment", "deployment_list", "Deployment added successfully.")


def normalize_schedule_day_code(value):
    code = str(value or "").strip().upper()

    if code in ["", "-", "_", "O", "OFF", "R", "REST"]:
        return None, None

    code_map = {
        "D": "D",
        "DAY": "D",
        "N": "N",
        "NIGHT": "N",
        "D/N": "D/N",
        "DN": "D/N",
        "DAY/NIGHT": "D/N",
        "PH": "PH",
        "PUBLIC HOLIDAY": "PH",
        "HOLIDAY": "PH",
    }

    if code in code_map:
        return code_map[code], None

    return None, code


def build_schedule_days(schedule_year, schedule_month, posted_values=None):
    posted_values = posted_values or {}
    last_day = monthrange(schedule_year, schedule_month)[1]
    days = []

    for day_number in range(1, 32):
        if day_number <= last_day:
            day_date = date(schedule_year, schedule_month, day_number)
            weekday = day_date.strftime("%a")
        else:
            weekday = ""

        days.append({
            "number": day_number,
            "weekday": weekday,
            "is_valid": day_number <= last_day,
            "value": posted_values.get(f"day_{day_number}", ""),
        })

    return days


def scheduled_guard_count_for_site(client, site_location, shift_date, exclude_guard_ids=None):
    exclude_guard_ids = exclude_guard_ids or []
    deployed_guard_ids = DeploymentGuard.objects.filter(
        deployment__client=client,
        deployment__site_location=site_location,
        deployment__status__in=ACTIVE_DEPLOYMENT_STATUSES,
        deployment__start_date__lte=shift_date,
        deployment_date=shift_date,
    ).filter(
        Q(deployment__end_date__isnull=True) | Q(deployment__end_date__gte=shift_date),
    ).values_list("guard_id", flat=True)

    scheduled_shifts = Shift.objects.filter(
        guard_id__in=deployed_guard_ids,
        start_time=shift_date,
    )

    if exclude_guard_ids:
        scheduled_shifts = scheduled_shifts.exclude(guard_id__in=exclude_guard_ids)

    return scheduled_shifts.values("guard_id").distinct().count()


def deployment_month_bounds(deployment, target_date):
    month_start = date(target_date.year, target_date.month, 1)
    month_end = date(target_date.year, target_date.month, monthrange(target_date.year, target_date.month)[1])
    schedule_start = max(month_start, deployment.start_date)
    schedule_end = min(month_end, deployment.end_date) if deployment.end_date else month_end
    return schedule_start, schedule_end


def has_protected_attendance_for_guard(guard, deployment_date):
    return Attendance.objects.filter(
        guard=guard,
        attendance_date=deployment_date,
    ).filter(
        Q(check_in_time__isnull=False) |
        Q(check_out_time__isnull=False)
    ).exists()


def generate_monthly_deployment_guards(deployment, guards, target_date, shift_type='D'):
    schedule_start, schedule_end = deployment_month_bounds(deployment, target_date)
    result = {
        "created": 0,
        "existing": 0,
        "protected": 0,
        "outside_range": schedule_start > schedule_end,
        "start": schedule_start,
        "end": schedule_end,
    }

    if result["outside_range"]:
        return result

    current_date = schedule_start
    while current_date <= schedule_end:
        for guard in guards:
            assignment, created = DeploymentGuard.objects.get_or_create(
                deployment=deployment,
                guard=guard,
                deployment_date=current_date,
                defaults={
                    "shift_type": shift_type or 'D',
                },
            )

            if created:
                result["created"] += 1
            elif has_protected_attendance_for_guard(guard, current_date):
                result["protected"] += 1
            else:
                if shift_type and assignment.shift_type != shift_type:
                    assignment.shift_type = shift_type
                    assignment.save(update_fields=["shift_type"])
                result["existing"] += 1

        current_date += timedelta(days=1)

    return result


@login_required
def program_guard(request):
    today = timezone.localdate()
    selected_year = clean_schedule_period_value(request.POST.get("schedule_year"), today.year)
    selected_month = clean_schedule_period_value(request.POST.get("schedule_month"), today.month)
    if selected_month < 1 or selected_month > 12:
        selected_month = today.month

    if request.method == "POST":
        form = ProgramGuardForm(request.POST)

        if form.is_valid():
            additional_guards = list(form.cleaned_data["additional_guards"])
            reliever_guard = form.cleaned_data["reliever_guard"]
            required_guards = form.cleaned_data["required_guards"]
            regular_guards = [form.cleaned_data["guard"], *additional_guards][:required_guards]
            valid_schedule = []
            invalid_codes = []
            last_day = monthrange(selected_year, selected_month)[1]

            for day_number in range(1, last_day + 1):
                raw_code = request.POST.get(f"day_{day_number}", "")
                shift_type, invalid_code = normalize_schedule_day_code(raw_code)

                if invalid_code:
                    invalid_codes.append(f"{invalid_code} on day {day_number}")
                    continue

                if shift_type:
                    shift_date = date(selected_year, selected_month, day_number)
                    valid_schedule.append((shift_date, shift_type))

            if invalid_codes:
                messages.error(request, f"Unsupported schedule code(s): {', '.join(invalid_codes)}. Use D, N, D/N, PH, O, or R.")
            elif not valid_schedule:
                messages.error(request, "Enter at least one scheduled duty day using D, N, D/N, or PH.")
            else:
                with transaction.atomic():
                    submitted_deployment = form.save(commit=False)
                    deployment, _ = get_or_create_deployment_for_schedule(
                        form.cleaned_data["guard"],
                        submitted_deployment.client,
                        submitted_deployment.site_location,
                        submitted_deployment.start_date,
                        submitted_deployment.end_date,
                    )
                    deployment_by_guard = {form.cleaned_data["guard"].guard_id: deployment}

                    for guard in [*regular_guards, reliever_guard]:
                        guard_deployment, _ = get_or_create_deployment_for_schedule(
                            guard,
                            deployment.client,
                            deployment.site_location,
                            deployment.start_date,
                            deployment.end_date,
                        )
                        deployment_by_guard[guard.guard_id] = guard_deployment

                    created_shifts = 0
                    updated_shifts = 0
                    skipped_outside_deployment = 0
                    skipped_capacity = 0
                    created_daily_assignments = 0
                    reliever_days = 0
                    duty_day_count = 0
                    scheduled_guard_names = set()

                    for shift_date, shift_type in valid_schedule:
                        if shift_date < deployment.start_date:
                            skipped_outside_deployment += 1
                            continue

                        if deployment.end_date and shift_date > deployment.end_date:
                            skipped_outside_deployment += 1
                            continue

                        duty_day_count += 1
                        existing_count = scheduled_guard_count_for_site(
                            deployment.client,
                            deployment.site_location,
                            shift_date,
                            exclude_guard_ids=[guard.guard_id for guard in [*regular_guards, reliever_guard]],
                        )

                        scheduled_today = []
                        for guard_index, regular_guard in enumerate(regular_guards):
                            if existing_count + len(scheduled_today) >= required_guards:
                                skipped_capacity += 1
                                continue

                            is_rest_day = (duty_day_count + guard_index) % 7 == 0
                            scheduled_guard = reliever_guard if is_rest_day else regular_guard

                            if scheduled_guard in scheduled_today:
                                continue

                            if scheduled_guard == reliever_guard:
                                reliever_days += 1

                            _, shift_created = Shift.objects.update_or_create(
                                guard=scheduled_guard,
                                start_time=shift_date,
                                defaults={
                                    "shift_name": f"Scheduled Duty ({shift_type})",
                                    "end_time": shift_date,
                                    "shift_type": shift_type,
                                },
                            )

                            if shift_created:
                                created_shifts += 1
                            else:
                                updated_shifts += 1

                            guard_deployment = deployment_by_guard.get(scheduled_guard.guard_id, deployment)
                            _, assignment_created = ensure_guard_deployment_for_date(
                                guard_deployment,
                                scheduled_guard,
                                shift_date,
                                shift_type,
                            )
                            if assignment_created:
                                created_daily_assignments += 1

                            scheduled_today.append(scheduled_guard)
                            scheduled_guard_names.add(scheduled_guard.full_name)

                log_action(request, "CREATE", f"Created scheduled deployment {deployment}.")
                messages.success(
                    request,
                    f"Scheduled {len(scheduled_guard_names)} guard(s) on {deployment.site_location}. "
                    f"Created {created_shifts} shift(s), updated {updated_shifts} shift(s), "
                    f"created {created_daily_assignments} daily deployment assignment(s), "
                    f"and assigned {reliever_days} reliever day(s) to {reliever_guard.full_name}."
                )
                if skipped_outside_deployment:
                    messages.warning(request, f"Skipped {skipped_outside_deployment} day(s) outside the deployment date range.")
                if skipped_capacity:
                    messages.warning(request, f"Skipped {skipped_capacity} day(s) because the schedule would exceed {required_guards} required guard(s) for that site.")
                return redirect("deployment_list")
    else:
        form = ProgramGuardForm(initial={
            "start_date": date(today.year, today.month, 1),
            "end_date": date(today.year, today.month, monthrange(today.year, today.month)[1]),
        })

    return render(request, "guardmanagementsystem/scheduled_guard_form.html", {
        "form": form,
        "title": "Scheduled Guard on Site",
        "current_month": selected_month,
        "current_year": selected_year,
        "month_choices": [(number, date(2000, number, 1).strftime("%B")) for number in range(1, 13)],
        "schedule_days": build_schedule_days(selected_year, selected_month, request.POST if request.method == "POST" else None),
    })


@login_required
def deployment_edit(request, id):
    deployment = get_object_or_404(Deployment, deployment_id=id)
    old_values = {
        "contract_id": deployment.contract_id,
        "client_id": deployment.client_id,
        "site_location": deployment.site_location,
        "start_date": deployment.start_date,
        "end_date": deployment.end_date,
        "status": deployment.status,
    }

    if request.method == "POST":
        form = DeploymentForm(request.POST, instance=deployment)
        if form.is_valid():
            deployment = form.save()
            changed_fields = [
                field for field, old_value in old_values.items()
                if getattr(deployment, field) != old_value
            ]
            log_action(request, "UPDATE", f"Updated {deployment}.")
            messages.success(request, "Deployment updated successfully.")

            if changed_fields:
                affected_assignments = DeploymentGuard.objects.filter(
                    deployment=deployment,
                    deployment_date__gte=timezone.localdate(),
                ).exists()
                if affected_assignments:
                    messages.warning(
                        request,
                        "This deployment already has generated guard deployment records. Review or regenerate the monthly schedule so attendance follows the updated deployment."
                    )

            return redirect("deployment_list")
    else:
        form = DeploymentForm(instance=deployment)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Deployment",
    })


@login_required
def deployment_delete(request, id):
    return delete_record(request, Deployment, {"deployment_id": id}, "Delete Deployment", "deployment_list", "Deployment deleted successfully.")


@login_required
def deployment_guard_list(request):
    deployment_guards = DeploymentGuard.objects.select_related(
        "deployment",
        "deployment__client",
        "deployment__contract",
        "guard",
    ).all().order_by("-deployment_date", "deployment__client__client_name", "guard__full_name")
    return show_records(request, deployment_guards, "guardmanagementsystem/deployment_guard_list.html", "deployment_guards")


@login_required
def deployment_guard_add(request):
    if request.method == "POST":
        form = DeploymentGuardBulkForm(request.POST)
        if form.is_valid():
            deployment = form.cleaned_data["deployment"]
            deployment_date = form.cleaned_data["deployment_date"]
            shift_type = form.cleaned_data.get("shift_type")
            guards = list(form.cleaned_data["guards"])

            with transaction.atomic():
                result = generate_monthly_deployment_guards(
                    deployment,
                    guards,
                    deployment_date,
                    shift_type,
                )

            log_action(
                request,
                "CREATE",
                f"Generated monthly guard deployment for {deployment} from {result['start']} to {result['end']}.",
            )

            if result["outside_range"]:
                messages.warning(request, "No guard deployment records were generated because the selected month is outside the deployment period.")
            else:
                messages.success(
                    request,
                    f"Generated monthly guard deployment from {result['start']:%b %d, %Y} to {result['end']:%b %d, %Y}. "
                    f"Created {result['created']} record(s)."
                )
                if result["existing"]:
                    messages.info(request, f"Skipped {result['existing']} existing deployment record(s).")
                if result["protected"]:
                    messages.warning(request, f"Skipped {result['protected']} record(s) that already had check-in/check-out or attendance.")
            return redirect("deployment_guard_list")
    else:
        form = DeploymentGuardBulkForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Generate Monthly Guard Deployment",
        "button_text": "Generate Month",
    })


@login_required
def deployment_guard_edit(request, id):
    return edit_record(
        request,
        DeploymentGuard,
        DeploymentGuardForm,
        {"deployment_guard_id": id},
        "Edit Guard Deployment",
        "deployment_guard_list",
        "Guard deployment updated successfully.",
    )

@login_required
def deployment_guard_delete(request, id):
    return delete_record(
        request,
        DeploymentGuard,
        {"deployment_guard_id": id},
        "Delete Guard Deployment",
        "deployment_guard_list",
        "Guard deployment deleted successfully.",
    )


@login_required
def asset_list(request):
    assets = Asset.objects.select_related("guard").prefetch_related("assignment_history").all()
    asset_status_counts = dict(
        assets.values("status").annotate(total=Count("asset_id")).values_list("status", "total")
    )
    total_assets = assets.count()
    issued_assets = asset_status_counts.get("Assigned", 0)
    available_assets = asset_status_counts.get("Available", 0)
    damaged_assets = asset_status_counts.get("Damaged", 0)
    lost_assets = asset_status_counts.get("Lost", 0)
    returned_assets = asset_status_counts.get("Returned", 0)
    refill_required = damaged_assets + lost_assets

    return render(request, "guardmanagementsystem/asset_list.html", {
        "assets": assets,
        "total_assets": total_assets,
        "issued_assets": issued_assets,
        "available_assets": available_assets,
        "damaged_assets": damaged_assets,
        "lost_assets": lost_assets,
        "returned_assets": returned_assets,
        "refill_required": refill_required,
    })


@login_required
def asset_add(request):
    return add_record(request, AssetForm, "Add Asset", "asset_list", "Asset added successfully.")


@login_required
def asset_edit(request, id):
    return edit_record(request, Asset, AssetForm, {"asset_id": id}, "Edit Asset", "asset_list", "Asset updated successfully.")


@login_required
def asset_delete(request, id):
    return delete_record(request, Asset, {"asset_id": id}, "Delete Asset", "asset_list", "Asset deleted successfully.")


@login_required
def asset_lifecycle_action(request, id, action):
    if request.method != "POST":
        return redirect("asset_list")

    asset = get_object_or_404(Asset.objects.select_related("guard"), asset_id=id)
    action_messages = {
        "return": "Asset marked as returned.",
        "damage": "Asset marked as damaged.",
        "lost": "Asset marked as lost.",
        "available": "Asset marked as available.",
    }

    if action not in action_messages:
        messages.error(request, "Invalid asset action.")
        return redirect("asset_list")

    previous_guard = asset.guard

    if action == "return":
        asset.guard = None
        asset.status = "Returned"
    elif action == "damage":
        asset.guard = None
        asset.status = "Damaged"
    elif action == "lost":
        asset.guard = None
        asset.status = "Lost"
    elif action == "available":
        if asset.guard:
            messages.error(request, "Unassign the asset before marking it available.")
            return redirect("asset_list")
        asset.status = "Available"

    asset.save()

    if previous_guard and action in ["return", "damage", "lost"]:
        AssetAssignmentHistory.objects.filter(
            asset=asset,
            guard=previous_guard,
            returned_date__isnull=True,
        ).update(
            returned_date=timezone.localdate(),
            condition_on_return=asset.status,
        )

    log_action(request, "UPDATE", f"{asset} {action_messages[action].lower()}")
    messages.success(request, action_messages[action])
    return redirect("asset_list")


@login_required
def iot_device_list(request):
    devices = IoTDevice.objects.all().order_by("device_number")
    return show_records(request, devices, "guardmanagementsystem/iot_device_list.html", "devices")


@login_required
def iot_device_add(request):
    return add_record(request, IoTDeviceForm, "Add IoT Device", "iot_device_list", "IoT device added successfully.")


@login_required
def iot_device_edit(request, id):
    return edit_record(request, IoTDevice, IoTDeviceForm, {"device_id": id}, "Edit IoT Device", "iot_device_list", "IoT device updated successfully.")


@login_required
def iot_device_delete(request, id):
    return delete_record(request, IoTDevice, {"device_id": id}, "Delete IoT Device", "iot_device_list", "IoT device deleted successfully.")


@login_required
def iot_swipe_options(request):
    if user_is_guard(request.user):
        return JsonResponse({"sites": [], "cards": []})

    device = IoTDevice.objects.filter(
        device_id=request.GET.get("device"),
        is_active=True,
    ).first()
    if not device:
        return JsonResponse({"sites": [], "cards": []})

    return JsonResponse(iot_device_swipe_options_data(device))


@login_required
def iot_swipe_attendance(request):
    is_guard_user = user_is_guard(request.user)
    current_guard = get_user_guard(request.user) if is_guard_user else None
    devices = IoTDevice.objects.filter(is_active=True).order_by("device_number")
    site_locations = (
        Deployment.objects.exclude(site_location="")
        .values_list("site_location", flat=True)
        .distinct()
        .order_by("site_location")
    )
    rfid_cards = []
    recent_attendances = recent_attendance_records()
    if current_guard:
        recent_attendances = recent_attendances.filter(
            Q(guard=current_guard) | Q(replacement_guard=current_guard)
        )
    elif is_guard_user:
        recent_attendances = recent_attendances.none()
    recent_attendances = recent_attendances[:10]

    for attendance in recent_attendances:
        decorate_attendance_swipe_status(attendance)
        earning_guard = attendance.replacement_guard or attendance.guard
        attendance.employee_label = f"{earning_guard.guard_number or '-'} - {earning_guard.full_name}"
        attendance.guard_profile_label = f"{earning_guard.guard_number or '-'} - {earning_guard.full_name}"

        deployment = get_attendance_deployment(attendance)
        shift = get_attendance_shift(attendance, deployment)
        attendance.client_label = deployment.client.client_name if deployment else "-"
        if deployment and deployment.contract_id:
            attendance.client_label = f"{attendance.client_label} - {deployment.contract.contract_number}"
        attendance.shift_label = get_shift_code(shift) if shift else "No shift"
        attendance.status_label = f"Replacement for {attendance.guard.full_name}" if attendance.replacement_guard else attendance.status
        attendance.payroll_guard_id = earning_guard.guard_id
        attendance.payroll_year = attendance.attendance_date.year
        attendance.payroll_month = attendance.attendance_date.month

    if request.method == "POST":
        if is_guard_user:
            messages.error(request, "Guards can view swipe attendance only.")
            return redirect("iot_swipe_attendance")

        device = IoTDevice.objects.filter(device_id=request.POST.get("device")).first()
        current_site = str(request.POST.get("site_location") or "").strip()
        card_id = request.POST.get("card_id")
        action = request.POST.get("action", "check_in")
        replacement_card_id = request.POST.get("replacement_card_id")
        replacement_guard_id = None

        if action in ["mark_absent", "absent", "replace"] and replacement_card_id == card_id:
            messages.error(request, "Replacement RFID card cannot be the same as the absent guard RFID card.")
            return redirect("iot_swipe_attendance")


        if not device:
            messages.error(request, "Select an active IoT device.")
        else:
            allowed_card_values = {
                card["value"]
                for card in iot_device_swipe_options_data(device)["cards"]
            }
            if card_id not in allowed_card_values:
                messages.error(request, "Select an RFID card for a guard assigned to the selected IoT device today.")
            elif replacement_card_id and replacement_card_id not in allowed_card_values:
                messages.error(request, "Select a replacement RFID card assigned to the selected IoT device today.")
            else:
                if not current_site:
                    current_site = infer_single_site_for_iot_device(device)

                if replacement_card_id:
                    replacement_assignments = find_active_assignments_by_card(
                        replacement_card_id,
                        current_site,
                        device,
                    )
                    replacement_guard_id = replacement_assignments[0].guard_id if len(replacement_assignments) == 1 else None
                success, message, _ = record_iot_attendance(
                    card_id,
                    current_site,
                    action,
                    replacement_guard_id,
                    device,
                )
                if success:
                    messages.success(request, message)
                else:
                    messages.error(request, message)

        return redirect("iot_swipe_attendance")

    return render(request, "guardmanagementsystem/iot_swipe_attendance.html", {
        "devices": devices,
        "site_locations": site_locations,
        "rfid_cards": rfid_cards,
        "recent_attendances": recent_attendances,
        "self_service_guard": current_guard,
    })


@csrf_exempt
def iot_attendance_api(request):
    if request.method != "POST":
        return JsonResponse({
            "success": False,
            "message": "Use POST to send RFID attendance."
        }, status=405)

    if request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({
                "success": False,
                "message": "Invalid JSON payload."
            }, status=400)
    else:
        payload = request.POST

    device_code = str(payload.get("device_code", "")).strip()
    api_key = str(payload.get("api_key", "")).strip()
    card_id = payload.get("card_id") or payload.get("rfid_card_number")
    current_site = str(payload.get("site_location") or payload.get("site") or payload.get("location") or "").strip()
    action = payload.get("action", "auto")
    replacement_guard_id = payload.get("replacement_guard_id")
    replacement_card_id = payload.get("replacement_card_id") or payload.get("replacement_rfid_card_number")


    device = IoTDevice.objects.filter(
        device_code=device_code,
        api_key=api_key,
        is_active=True,
    ).first()

    if not device:
        return JsonResponse({
            "success": False,
            "message": "Invalid or inactive IoT device."
        }, status=403)

    site_location = current_site or infer_single_site_for_iot_device(device)
    if replacement_card_id and not replacement_guard_id:
        replacement_assignments = find_active_assignments_by_card(replacement_card_id, site_location, device)
        replacement_guard_id = replacement_assignments[0].guard_id if len(replacement_assignments) == 1 else None

    success, message, attendance = record_iot_attendance(card_id, site_location, action, replacement_guard_id, device)
    data = {
        "success": success,
        "message": message,
        "device": device.device_code,
        "site_location": site_location,
    }

    if attendance:
        deployment = get_attendance_deployment(attendance)
        data.update({
            "guard": attendance.guard.full_name,
            "guard_number": attendance.guard.guard_number,
            "client": deployment.client.client_name if deployment else None,
            "shift": get_shift_code(get_attendance_shift(attendance, deployment)),
            "attendance_date": attendance.attendance_date.isoformat(),
            "status": attendance.status,
            "check_in_time": attendance.check_in_time.isoformat() if attendance.check_in_time else None,
            "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
            "recorded_swipes": decorate_attendance_swipe_status(attendance).recorded_swipe_count,
            "expected_hourly_swipes": attendance.expected_swipe_count,
            "missed_swipes": [row.expected_at.isoformat() for row in attendance.missed_swipes],
        })

    return JsonResponse(data, status=200 if success else 400)


@login_required
def shift_list(request):
    shifts = list(Shift.objects.select_related("guard").all())

    for shift in shifts:
        shift.shift_code = get_shift_code(shift)
        if shift.shift_code == "N":
            shift.time_in_display = "1800hrs"
            shift.time_out_display = "0700hrs"
            shift.shift_type_display = "Night"
        else:
            shift.time_in_display = "0700hrs"
            shift.time_out_display = "1800hrs"
            shift.shift_type_display = "Day"

    return render(request, "guardmanagementsystem/shift_list.html", {
        "shifts": shifts
    })


@login_required
def shift_add(request):
    return add_record(request, ShiftForm, "Add Shift", "shift_list", "Shift added successfully.")


@login_required
def shift_edit(request, id):
    return edit_record(request, Shift, ShiftForm, {"shift_id": id}, "Edit Shift", "shift_list", "Shift updated successfully.")


@login_required
def shift_delete(request, id):
    return delete_record(request, Shift, {"shift_id": id}, "Delete Shift", "shift_list", "Shift deleted successfully.")


@login_required
def salary_list(request):
    selected_guard = request.GET.get("guard", "")
    selected_year = request.GET.get("year", "")
    selected_month = request.GET.get("month", "")
    is_guard_user = user_is_guard(request.user)
    current_guard = get_user_guard(request.user) if is_guard_user else None
    if current_guard:
        selected_guard = str(current_guard.guard_id)
        salaries = build_recent_attendance_salary_rows(selected_guard, selected_year, selected_month)
    elif is_guard_user:
        salaries = []
    else:
        salaries = build_recent_attendance_salary_rows(selected_guard, selected_year, selected_month)
    return render(request, "guardmanagementsystem/salary_list.html", {
        "salaries": salaries,
        "selected_guard": selected_guard,
        "selected_year": selected_year,
        "selected_month": selected_month,
        "self_service_guard": current_guard,
    })

def calculate_uganda_paye(monthly_income):
    monthly_income = money(monthly_income)

    if monthly_income <= Decimal("235000"):
        return money(0)

    if monthly_income <= Decimal("335000"):
        return money((monthly_income - Decimal("235000")) * Decimal("0.10"))

    if monthly_income <= Decimal("410000"):
        return money(Decimal("10000") + ((monthly_income - Decimal("335000")) * Decimal("0.20")))

    paye = Decimal("25000") + ((monthly_income - Decimal("410000")) * Decimal("0.30"))
    if monthly_income > Decimal("10000000"):
        paye += (monthly_income - Decimal("10000000")) * Decimal("0.10")

    return money(paye)


def calculate_local_service_tax_annual(monthly_income):
    monthly_income = money(monthly_income)

    if monthly_income <= Decimal("100000"):
        return money(0)
    if monthly_income <= Decimal("200000"):
        return money(5000)
    if monthly_income <= Decimal("300000"):
        return money(10000)
    if monthly_income <= Decimal("400000"):
        return money(20000)
    if monthly_income <= Decimal("500000"):
        return money(30000)
    if monthly_income <= Decimal("600000"):
        return money(40000)
    if monthly_income <= Decimal("700000"):
        return money(60000)
    if monthly_income <= Decimal("800000"):
        return money(70000)
    if monthly_income <= Decimal("900000"):
        return money(80000)
    if monthly_income <= Decimal("1000000"):
        return money(90000)
    return money(100000)


def build_salary_payroll_context(salary):
    gross_pay = money(salary.basic_pay) + money(salary.allowances)
    paye = calculate_uganda_paye(gross_pay)
    nssf_employee = money(gross_pay * Decimal("0.05"))
    nssf_employer = money(gross_pay * Decimal("0.10"))
    lst_annual = calculate_local_service_tax_annual(gross_pay)
    month_number = MONTH_NUMBERS.get(salary.month, 0)
    lst_deduction = money(lst_annual / Decimal("4")) if month_number in [7, 8, 9, 10] else money(0)
    manual_deductions = money(salary.deductions)
    statutory_deductions = paye + nssf_employee + lst_deduction
    total_deductions = statutory_deductions + manual_deductions
    net_payable = gross_pay - total_deductions

    return {
        "gross_pay": money(gross_pay),
        "paye": paye,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "lst_annual": lst_annual,
        "lst_deduction": lst_deduction,
        "manual_deductions": manual_deductions,
        "statutory_deductions": money(statutory_deductions),
        "total_deductions": money(total_deductions),
        "net_payable": money(net_payable),
    }


@login_required
def salary_payslip_individual(request, id):
    salary = get_object_or_404(
        Salary.objects.select_related("guard"),
        salary_id=id
    )
    return render(request, "guardmanagementsystem/payslip.html", {
        "salary": salary,
        "payroll": build_salary_payroll_context(salary),
        "title": "Individual Payslip",
    })


@login_required
def salary_payslip_from_attendance(request, guard_id, year, month):
    is_guard_user = user_is_guard(request.user)
    current_guard = get_user_guard(request.user) if is_guard_user else None
    if is_guard_user and (not current_guard or current_guard.guard_id != guard_id):
        return HttpResponseForbidden("You can only view your own payslip.")

    salary = next(
        (
            row for row in build_recent_attendance_salary_rows()
            if row.guard_id == guard_id and row.year == year and row.month_number == month
        ),
        None,
    )

    if not salary:
        raise Http404("No attendance-based payroll row found for this guard and month.")

    return render(request, "guardmanagementsystem/payslip.html", {
        "salary": salary,
        "payroll": build_salary_payroll_context(salary),
        "title": "Attendance Payslip",
    })


@login_required
def salary_attendance_summary(request, guard_id, year, month):
    guard = get_object_or_404(Guard, guard_id=guard_id)
    if user_is_guard(request.user):
        current_guard = get_user_guard(request.user)
        if not current_guard or current_guard.guard_id != guard.guard_id:
            return HttpResponseForbidden("You can only view your own attendance summary.")

    if month < 1 or month > 12:
        raise Http404("Invalid month.")

    summary = build_guard_month_attendance_summary(guard, year, month)
    return render(request, "guardmanagementsystem/salary_attendance_summary.html", {
        "summary": summary,
    })


@login_required
def salary_payslip_general(request):
    salaries = build_recent_attendance_salary_rows()
    payroll_rows = [
        {
            "salary": salary,
            "payroll": build_salary_payroll_context(salary),
        }
        for salary in salaries
    ]
    totals = {
        "basic_pay": money(sum(row["salary"].basic_pay for row in payroll_rows)),
        "allowances": money(sum(row["salary"].allowances for row in payroll_rows)),
        "paye": money(sum(row["payroll"]["paye"] for row in payroll_rows)),
        "nssf_employee": money(sum(row["payroll"]["nssf_employee"] for row in payroll_rows)),
        "lst_deduction": money(sum(row["payroll"]["lst_deduction"] for row in payroll_rows)),
        "manual_deductions": money(sum(row["payroll"]["manual_deductions"] for row in payroll_rows)),
        "total_deductions": money(sum(row["payroll"]["total_deductions"] for row in payroll_rows)),
        "net_payable": money(sum(row["payroll"]["net_payable"] for row in payroll_rows)),
    }
    totals["gross_pay"] = money(totals["basic_pay"] + totals["allowances"])
    return render(request, "guardmanagementsystem/payslip_general.html", {
        "salaries": salaries,
        "payroll_rows": payroll_rows,
        "totals": totals,
        "title": "General Payslip",
    })


@login_required
def salary_add(request):
    if request.method == "POST":
        form = SalaryForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Salary added successfully.")
            return redirect("salary_list")
    else:
        initial = {}
        guard_id = request.GET.get("guard")

        if guard_id:
            initial = {
                "employee_type": "Guard",
                "guard": guard_id,
            }

        form = SalaryForm(initial=initial)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Salary"
    })


@login_required
def salary_edit(request, id):
    return edit_record(request, Salary, SalaryForm, {"salary_id": id}, "Edit Salary", "salary_list", "Salary updated successfully.")


@login_required
def salary_delete(request, id):
    return delete_record(request, Salary, {"salary_id": id}, "Delete Salary", "salary_list", "Salary deleted successfully.")


@login_required
def incident_list(request):
    incidents = Incident.objects.select_related("guard").all()
    return render(request, "guardmanagementsystem/incident_list.html", {
        "incidents": incidents
    })


@login_required
def incident_pdf_report(request, id):
    incident = get_object_or_404(
        Incident.objects.select_related("guard"),
        incident_id=id
    )
    incident_time = incident.incident_time.strftime("%I:%M %p") if incident.incident_time else "-"
    generated_at = timezone.localtime().strftime("%Y-%m-%d %I:%M %p")
    pdf_bytes = build_simple_pdf(
        f"Standard Incident Report - IR-{incident.incident_id}",
        [
            ("Document Control", [
                ("Report Reference", f"IR-{incident.incident_id}"),
                ("Document Type", "Standard Security Incident Report"),
                ("Generated On", generated_at),
                ("Confidentiality", "Internal use only"),
                ("Current Status", incident.status),
            ]),
            ("Incident Classification", [
                ("Incident Category", incident.incident_type),
                ("Severity / Priority", "To be assessed"),
                ("Site / Location", incident.location),
                ("Incident Date", incident.incident_date),
                ("Incident Time", incident_time),
            ]),
            ("Reporting Guard / Officer", [
                ("Name", incident.guard.full_name),
                ("Employee Number", incident.guard.employee_number or "-"),
                ("Phone", incident.guard.phone),
                ("Email", incident.guard.email),
                ("Employment Status", incident.guard.status),
            ]),
            ("Reported By / Issuing Officer", [
                ("Name", incident.reporter_name),
            ]),
            ("Person(s) Involved", [
                ("Primary Person Involved", incident.involved_full_name),
                ("Witnesses", "Not recorded"),
                ("Injuries / Damage", "Not recorded"),
            ]),
            ("Incident Narrative", [
                ("Summary of Facts", incident.description),
                ("Further Comments", incident.further_comments or "-"),
            ]),
            ("Immediate Action Taken", [
                ("Action Taken at Scene", "Not recorded"),
                ("Notification Made To", "-"),
                ("Police / External Reference", "Not recorded"),
            ]),
            ("Evidence and Attachments", [
                ("Photos / CCTV / Documents", "Not recorded"),
                ("Assets Affected", "Not recorded"),
            ]),
            ("Follow Up and Closure", [
                ("Corrective Action Required", "To be completed"),
                ("Responsible Person", "-"),
                ("Closure Notes", "To be completed after review"),
            ]),
        ]
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="incident_report_IR-{incident.incident_id}.pdf"'
    return response


@login_required
def incident_add(request):
    initial = {
        "incident_date": timezone.localdate(),
        "incident_time": timezone.localtime().time().replace(second=0, microsecond=0),
        "status": "Pending",
    }

    if request.method == "POST":
        form = IncidentForm(request.POST)

        if form.is_valid():
            incident = form.save()
            messages.success(request, "Incident report submitted successfully.")

            try:
                email_sent, email_message = send_incident_notification(incident)
                if email_sent:
                    messages.success(request, email_message)
                else:
                    messages.warning(request, email_message)
            except Exception as error:
                messages.warning(request, f"Incident was saved, but email notification failed: {error}")

            return redirect("incident_list")
    else:
        form = IncidentForm(initial=initial)

    return render(request, "guardmanagementsystem/incident_form.html", {
        "form": form,
        "title": "Incident Report Form",
        "button_text": "Report Now!"
    })


@login_required
def incident_edit(request, id):
    return edit_record(
        request,
        Incident,
        IncidentForm,
        {"incident_id": id},
        "Edit Incident Report",
        "incident_list",
        "Incident updated successfully.",
        template_name="guardmanagementsystem/incident_form.html",
        extra_context={"button_text": "Report Now!"}
    )


@login_required
def incident_delete(request, id):
    return delete_record(request, Incident, {"incident_id": id}, "Delete Incident", "incident_list", "Incident deleted successfully.")

