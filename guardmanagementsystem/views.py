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
from urllib.parse import urlencode
import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace


from .models import (
    AdvanceRequest,
    Asset,
    Attendance,
    AuditLog,
    Client,
    ClientCommunication,
    Deployment,
    DisciplinaryAction,
    Guard,
    Incident,
    IoTDevice,
    RFIDCard,
    Salary,
    Shift,
    Supervisor,
    recompute_guard_salary_for_month,
)
from .role_access import user_can_access
from .forms import (
    AdvanceRequestForm,
    AssetForm,
    AttendanceForm,
    ClientForm,
    ClientCommunicationForm,
    DeploymentForm,
    DisciplinaryActionForm,
    EmailOrUsernameAuthenticationForm,
    GuardSelfAdvanceRequestForm,
    GuardForm,
    IncidentForm,
    IoTDeviceForm,
    ProgramGuardForm,
    RemoteAdvanceRequestForm,
    RFIDCardForm,
    SalaryForm,
    ShiftForm,
    SignupForm,
    SupervisorForm,
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
    if get_user_client(user):
        return "client_communication_list"
    if user_can_access(user, "dashboard"):
        return "dashboard"
    return "advance_request_list"


def signin(request):
    if request.user.is_authenticated and request.method == "GET":
        return redirect(user_home_url_name(request.user))

    if request.method == "POST":
        form = EmailOrUsernameAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            log_action(request, "SIGNIN", "User signed in.")
            if get_user_supervisor(user):
                record_supervisor_login_attendance(user)
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
        "helper_link": "forgot_password",
        "helper_text": "Forgot password?"
    })


@login_required
def signout(request):
    log_action(request, "LOGOUT", "User signed out.")
    logout(request)
    messages.success(request, "Signed out successfully.")
    return redirect("signin")


def forgot_password(request):
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
    supervisor = incident.reported_by

    if not supervisor or not supervisor.email:
        return False, "Select a supervisor with an email address to send notification."

    subject = f"New Incident Report: {incident.incident_type}"
    incident_time = incident.incident_time.strftime("%I:%M %p") if incident.incident_time else "-"
    message = "\n".join([
        "A new incident report has been submitted.",
        "",
        f"Guard: {incident.guard.full_name}",
        f"Date: {incident.incident_date}",
        f"Time: {incident_time}",
        f"Location: {incident.location}",
        f"Nature: {incident.incident_type}",
        f"Involved person: {incident.involved_full_name}",
        f"Issued by: {incident.reporter_name}",
        "",
        "Incident details:",
        incident.description,
        "",
        "Further comments:",
        incident.further_comments or "-",
    ])

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [supervisor.email],
        fail_silently=False,
    )
    return True, f"Email notification sent to {supervisor.email}."


def has_active_deployment(guard, work_date, site=None):
    if not guard or not work_date:
        return False

    deployments = Deployment.objects.filter(
        guard=guard,
        status="Active",
        start_date__lte=work_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=work_date)
    )

    if site:
        deployments = deployments.filter(site_location=site)

    return deployments.exists()


def get_matching_deployment(guard, work_date, site=None, client_name=None):
    if not guard or not work_date:
        return None

    deployments = Deployment.objects.select_related("client").filter(
        guard=guard,
        status="Active",
        start_date__lte=work_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=work_date)
    )

    if site:
        deployments = deployments.filter(site_location=site)

    if client_name:
        deployments = deployments.filter(client__client_name__iexact=client_name)

    return deployments.first()


def get_or_create_deployment_for_schedule(guard, client, site_location, start_date, end_date):
    deployment = Deployment.objects.filter(
        guard=guard,
        client=client,
        site_location=site_location,
        start_date=start_date,
        end_date=end_date,
    ).order_by("-deployment_id").first()

    if deployment:
        created = False
    else:
        deployment = Deployment.objects.create(
            guard=guard,
            client=client,
            site_location=site_location,
            start_date=start_date,
            end_date=end_date,
            status="Active",
        )
        created = True

    if deployment.status != "Active":
        deployment.status = "Active"
        deployment.save(update_fields=["status"])

    return deployment, created


def recent_attendance_records():
    return Attendance.objects.select_related(
        "guard",
        "guard__supervisor",
        "replacement_guard",
        "replacement_guard__supervisor",
        "swiped_by_supervisor",
    ).filter(
        Q(check_in_time__isnull=False) | Q(check_out_time__isnull=False)
    ).order_by("-attendance_date", "-check_out_time", "-check_in_time", "-attendance_id")


def build_recent_attendance_salary_rows(guard_id=None, year=None, month=None):
    salary_rows_by_guard_month = {}
    guard_id = str(guard_id or "").strip()
    year = clean_schedule_period_value(year, 0) if year else None
    month = clean_schedule_period_value(month, 0) if month else None

    for attendance in recent_attendance_records().order_by("attendance_date"):
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
                supervisor=None,
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


def get_user_supervisor(user):
    if not user.is_authenticated:
        return None
    return Supervisor.objects.filter(user=user).first()


def record_supervisor_login_attendance(user):
    supervisor = get_user_supervisor(user)
    if not supervisor or not supervisor.guard_id:
        return None

    today = timezone.localdate()
    current_time = timezone.localtime().time()
    attendance, created = Attendance.objects.get_or_create(
        guard=supervisor.guard_id,
        attendance_date=today,
        defaults={
            "status": "Present",
            "check_in_time": current_time,
            "swiped_by_supervisor": supervisor,
            "absence_reason": "Supervisor login attendance",
        },
    )

    update_fields = []
    if not attendance.check_in_time:
        attendance.check_in_time = current_time
        update_fields.append("check_in_time")

    if attendance.status != "Present":
        attendance.status = "Present"
        update_fields.append("status")

    if attendance.swiped_by_supervisor_id != supervisor.supervisor_id:
        attendance.swiped_by_supervisor = supervisor
        update_fields.append("swiped_by_supervisor")

    if attendance.absence_reason != "Supervisor login attendance":
        attendance.absence_reason = "Supervisor login attendance"
        update_fields.append("absence_reason")

    if update_fields:
        attendance.save(update_fields=update_fields)

    return attendance


def record_iot_attendance(card_id, site_location, action="check_in", replacement_guard_id=None, swiped_by_supervisor_id=None):
    card_id = str(card_id or "").strip()
    site_location = str(site_location or "").strip()
    action = str(action or "check_in").strip().lower()

    if not card_id:
        return False, "RFID card number is required.", None

    if not site_location:
        return False, "Site location is required.", None

    guard = Guard.objects.filter(
        Q(rfid_card__card_uid=card_id) |
        Q(rfid_card__card_number=card_id) |
        Q(rfid_card_number=card_id)
    ).select_related("rfid_card", "supervisor").first()
    if not guard:
        return False, "No guard is registered with this RFID card.", None

    if guard.rfid_card and guard.rfid_card.status != "Active":
        return False, "This RFID card is not active.", None

    if guard.status != "Active":
        return False, "This guard is not active.", None

    swiped_by_supervisor = None
    if swiped_by_supervisor_id:
        swiped_by_supervisor = Supervisor.objects.filter(supervisor_id=swiped_by_supervisor_id).first()
        if not swiped_by_supervisor:
            return False, "Select a valid supervisor.", None

        if guard.supervisor_id != swiped_by_supervisor.supervisor_id:
            return False, f"{swiped_by_supervisor.full_name} is not assigned to supervise {guard.full_name}.", None

    today = timezone.localdate()
    current_time = timezone.localtime().time()

    if not has_active_deployment(guard, today, site_location):
        return False, "Guard is not actively deployed at this site today.", None

    if action in ["mark_absent", "absent", "replace"]:
        replacement_guard = Guard.objects.select_related("supervisor").filter(guard_id=replacement_guard_id, status="Active").first()
        if not replacement_guard:
            return False, "Select an active replacement guard.", None

        if replacement_guard == guard:
            return False, "Replacement guard cannot be the same as the absent assigned RFID guard.", None

        if swiped_by_supervisor and replacement_guard.supervisor_id != swiped_by_supervisor.supervisor_id:
            return False, f"{swiped_by_supervisor.full_name} is not assigned to supervise replacement guard {replacement_guard.full_name}.", None

        attendance, _ = Attendance.objects.get_or_create(
            guard=guard,
            attendance_date=today,
            defaults={"status": "Present"},
        )
        attendance.status = "Absent"
        attendance.replacement_guard = replacement_guard
        attendance.swiped_by_supervisor = swiped_by_supervisor
        attendance.absence_reason = "Assigned RFID absent - replaced via IoT"
        attendance.check_in_time = current_time
        attendance.save()
        return True, f"{guard.full_name} marked absent. Replacement recorded for {replacement_guard.full_name}.", attendance

    attendance, created = Attendance.objects.get_or_create(
        guard=guard,
        attendance_date=today,
        defaults={
            "status": "Present",
            "check_in_time": current_time,
            "swiped_by_supervisor": swiped_by_supervisor,
        },
    )

    if action == "check_out":
        attendance.check_out_time = current_time
        if swiped_by_supervisor:
            attendance.swiped_by_supervisor = swiped_by_supervisor
        attendance.save()
        return True, f"Check-out recorded for {guard.full_name}.", attendance

    if created:
        return True, f"Check-in recorded for {guard.full_name}.", attendance

    if not attendance.check_in_time:
        attendance.check_in_time = current_time
        attendance.status = "Present"
        attendance.swiped_by_supervisor = swiped_by_supervisor
        attendance.save()
        return True, f"Check-in recorded for {guard.full_name}.", attendance

    if swiped_by_supervisor and attendance.swiped_by_supervisor_id != swiped_by_supervisor.supervisor_id:
        attendance.swiped_by_supervisor = swiped_by_supervisor
        attendance.save(update_fields=["swiped_by_supervisor"])

    return True, f"{guard.full_name} already has attendance for today.", attendance


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

    if shift and shift.shift_name:
        code_match = re.search(r"\(([A-Z]+)\)\s*$", shift.shift_name.strip(), re.IGNORECASE)
        if code_match:
            return shift_code_map.get(code_match.group(1).upper(), code_match.group(1).upper())

    return "D"


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
    )).strip()
    guard_name = str(get_row_value(row, "name", "guard", "guard_name", "full_name")).strip()

    if employee_number:
        guard = Guard.objects.filter(employee_number=employee_number).first()
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
    has_basic_columns = all(value in headers for value in ["guard", "client", "location"])
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


def build_scheduled_guard_report(deployment_area="", site="", start_date="", end_date=""):
    today = timezone.localdate()
    deployments = Deployment.objects.select_related("client", "guard").all()

    if deployment_area:
        deployments = deployments.filter(client_id=deployment_area)

    if site:
        deployments = deployments.filter(site_location=site)

    report_start = parse_date(start_date or "") or date(today.year, today.month, 1)
    report_end = parse_date(end_date or "") or date(
        report_start.year,
        report_start.month,
        monthrange(report_start.year, report_start.month)[1]
    )
    day_numbers = list(range(1, monthrange(report_start.year, report_start.month)[1] + 1))
    day_headings = [
        {
            "number": day_number,
            "weekday": date(report_start.year, report_start.month, day_number).strftime("%a"),
        }
        for day_number in day_numbers
    ]

    deployment_guard_ids = list(deployments.values_list("guard_id", flat=True))
    shifts = Shift.objects.select_related("guard").filter(
        guard_id__in=deployment_guard_ids,
        start_time__gte=report_start,
        start_time__lte=report_end,
    )

    report_index = {}
    for shift in shifts:
        deployment = deployments.filter(
            guard=shift.guard,
            start_date__lte=shift.start_time,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=shift.start_time)
        ).first()
        if not deployment:
            continue

        key = (shift.guard_id, deployment.client_id, deployment.site_location)
        row = report_index.setdefault(key, {
            "guard": shift.guard.full_name,
            "client": deployment.client.client_name,
            "site": deployment.site_location,
            "shift_type": "",
            "cells": {day_number: "" for day_number in day_numbers},
        })

        shift_code = get_shift_code(shift)
        if shift.start_time.month == report_start.month and shift.start_time.year == report_start.year:
            row["cells"][shift.start_time.day] = shift_code

    for row in report_index.values():
        used_codes = sorted({code for code in row["cells"].values() if code})
        row["shift_type"] = used_codes[0] if len(used_codes) == 1 else ("Mixed" if used_codes else "-")
        row["day_cells"] = [row["cells"][day_number] for day_number in day_numbers]

    report_rows = sorted(
        report_index.values(),
        key=lambda row: (row["site"], row["guard"])
    )

    return {
        "day_headings": day_headings,
        "day_numbers": day_numbers,
        "report_rows": report_rows,
        "report_start": report_start,
        "report_end": report_end,
        "report_month": report_start.strftime("%B %Y"),
    }


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
        attendance_created_count = 0
        attendance_existing_count = 0
        capacity_skipped_count = 0
        skipped_rows = []

        for row_number, row in enumerate(rows, start=2):
            normalized_row = {normalize_header(key): value for key, value in row.items()}
            shift_date_value = get_row_value(normalized_row, "shift_date", "date", "date_scheduled", "start_date")
            shift_type_value = get_row_value(normalized_row, "shift_type", "shift", "d_n", "d_or_n")
            shift_name = str(get_row_value(normalized_row, "shift_name", "name") or "Imported Shift").strip()
            attendance_status_value = get_row_value(normalized_row, "attendance_status", "status", "attendance", "present")
            absence_reason = str(get_row_value(normalized_row, "absence_reason", "reason") or "").strip()
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

            attendance = Attendance.objects.filter(guard=guard, attendance_date=shift_date).first()
            if attendance:
                attendance_existing_count += 1
            else:
                Attendance.objects.create(
                    guard=guard,
                    attendance_date=shift_date,
                    status=normalize_attendance_status(attendance_status_value),
                    absence_reason=absence_reason,
                )
                attendance_created_count += 1

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

        if attendance_created_count:
            messages.success(request, f"Added {attendance_created_count} uploaded row(s) to Attendance.")
        elif attendance_existing_count:
            messages.info(request, "Uploaded rows were already available in Attendance.")

        if skipped_rows:
            messages.warning(request, f"Skipped row(s): {', '.join(str(row) for row in skipped_rows)} because they did not match an existing guard/client/site.")

        if capacity_skipped_count:
            messages.warning(request, f"Skipped {capacity_skipped_count} schedule row(s) because they exceeded {required_guards} required guard(s) for the site.")

        return redirect("attendance_list")

    return render(request, "guardmanagementsystem/shift_import.html", {
        "current_month": timezone.localdate().month,
        "current_year": timezone.localdate().year,
        "required_guards": 1,
    })


@login_required
def schedule_report(request):
    deployment_area = request.GET.get("deployment_area", "")
    site = request.GET.get("site", "")
    start_date = request.GET.get("start_date", "")
    end_date = request.GET.get("end_date", "")
    clients = Client.objects.all().order_by("client_name")
    sites = (
        Deployment.objects.exclude(site_location="")
        .values_list("site_location", flat=True)
        .distinct()
        .order_by("site_location")
    )
    report_data = build_scheduled_guard_report(deployment_area, site, start_date, end_date)

    return render(request, "guardmanagementsystem/schedule_report.html", {
        "clients": clients,
        "sites": sites,
        "day_headings": report_data["day_headings"],
        "report_rows": report_data["report_rows"],
        "report_month": report_data["report_month"],
        "export_query": urlencode({
            "deployment_area": deployment_area,
            "site": site,
            "start_date": report_data["report_start"].isoformat(),
            "end_date": report_data["report_end"].isoformat(),
        }),
        "selected_deployment_area": deployment_area,
        "selected_site": site,
        "selected_start_date": report_data["report_start"].isoformat(),
        "selected_end_date": report_data["report_end"].isoformat(),
    })


@login_required
def schedule_report_csv_export(request):
    report_data = build_scheduled_guard_report(
        request.GET.get("deployment_area", ""),
        request.GET.get("site", ""),
        request.GET.get("start_date", ""),
        request.GET.get("end_date", ""),
    )
    response = HttpResponse(content_type="text/csv")
    filename = f"scheduled_guard_report_{report_data['report_start'].strftime('%Y_%m')}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Guard",
        "Client",
        "Location",
        "Shift type",
        *[day["number"] for day in report_data["day_headings"]],
    ])
    writer.writerow([
        "",
        "",
        "",
        "",
        *[day["weekday"] for day in report_data["day_headings"]],
    ])

    for row in report_data["report_rows"]:
        writer.writerow([
            row["guard"],
            row["client"],
            row["site"],
            row["shift_type"],
            *row["day_cells"],
        ])

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
def attendance_query_report(request):
    employee_number = request.GET.get("employee_number", "").strip()
    start_date = request.GET.get("start_date", "")
    end_date = request.GET.get("end_date", "")

    attendances = Attendance.objects.select_related("guard").all()

    if employee_number:
        attendances = attendances.filter(
            Q(guard__guard_number__icontains=employee_number) |
            Q(guard__full_name__icontains=employee_number)
        )

    if start_date:
        attendances = attendances.filter(attendance_date__gte=start_date)

    if end_date:
        attendances = attendances.filter(attendance_date__lte=end_date)

    guard_ids = list(attendances.values_list("guard_id", flat=True))
    deployments = Deployment.objects.select_related("client").filter(guard_id__in=guard_ids)
    shifts = Shift.objects.filter(guard_id__in=guard_ids)

    deployment_by_guard = {}
    for deployment in deployments:
        deployment_by_guard.setdefault(deployment.guard_id, deployment)

    shift_by_guard_date = {}
    for shift in shifts:
        shift_by_guard_date.setdefault((shift.guard_id, shift.start_time), shift)

    report_rows = []
    for attendance in attendances.order_by("-attendance_date", "guard__full_name"):
        deployment = deployment_by_guard.get(attendance.guard_id)
        shift = shift_by_guard_date.get((attendance.guard_id, attendance.attendance_date))

        report_rows.append({
            "date_scheduled": attendance.attendance_date,
            "site_scheduled": deployment.site_location if deployment else "-",
            "scheduled_employee": attendance.guard,
            "shift_type": get_shift_code(shift),
            "attendance": attendance.status,
            "replacement": "-",
            "recorded_by": "-",
            "date_recorded": attendance.attendance_date,
        })

    return render(request, "guardmanagementsystem/attendance_query_report.html", {
        "report_rows": report_rows,
        "selected_employee_number": employee_number,
        "selected_start_date": start_date,
        "selected_end_date": end_date,
    })


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
    total_supervisors = Supervisor.objects.count()
    total_guards = Guard.objects.count()
    total_clients = Client.objects.count()
    total_deployments = Deployment.objects.count()
    total_assets = Asset.objects.count()
    total_shifts = Shift.objects.count()
    total_salaries = Salary.objects.count()
    total_incidents = Incident.objects.count()
    total_disciplinary_actions = DisciplinaryAction.objects.count()
    total_advance_requests = AdvanceRequest.objects.count()

    context = {
        "total_supervisors": total_supervisors,
        "total_guards": total_guards,
        "total_clients": total_clients,
        "total_deployments": total_deployments,
        "total_assets": total_assets,
        "total_shifts": total_shifts,
        "total_salaries": total_salaries,
        "total_incidents": total_incidents,
        "total_disciplinary_actions": total_disciplinary_actions,
        "total_advance_requests": total_advance_requests,

        # Chart data
        "chart_labels": [
            "Supervisors",
            "Guards",
            "Clients",
            "Deployments",
            "Assets",
            "Shifts",
            "Salaries",
            "Incidents",
            "Disciplinary",
            "Advances",
        ],

        "chart_values": [
            total_supervisors,
            total_guards,
            total_clients,
            total_deployments,
            total_assets,
            total_shifts,
            total_salaries,
            total_incidents,
            total_disciplinary_actions,
            total_advance_requests,
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
    assigned_guard = Guard.objects.filter(rfid_card=card).first()

    if assigned_guard and assigned_guard.status != "Active":
        messages.error(request, "Cannot activate this RFID card because the assigned guard is not active.")
        return redirect("rfid_card_list")

    card.status = "Active"
    card.save(update_fields=["status"])
    log_action(request, "UPDATE", f"Remotely activated RFID card {card.card_number}.")
    messages.success(request, f"RFID card {card.card_number} activated.")
    return redirect("rfid_card_list")


@login_required
def supervisor_list(request):
    return show_records(request, Supervisor.objects.all(), "guardmanagementsystem/supervisor_list.html", "supervisors")


@login_required
def supervisor_add(request):
    return add_record(request, SupervisorForm, "Add Supervisor", "supervisor_list", "Supervisor added successfully")


@login_required
def supervisor_edit(request, id):
    return edit_record(request, Supervisor, SupervisorForm, {"supervisor_id": id}, "Edit Supervisor", "supervisor_list", "Supervisor updated successfully")


@login_required
def supervisor_delete(request, id):
    return delete_record(request, Supervisor, {"supervisor_id": id}, "Delete Supervisor", "supervisor_list", "Supervisor deleted successfully")

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


def send_client_communication_notification(request, communication):
    supervisor = communication.review_supervisor
    if not supervisor or not supervisor.email:
        return False, "Supervisor notification is available in the portal."

    review_url = request.build_absolute_uri("/client-communications/")
    subject = f"Client {communication.message_type}: {communication.subject}"
    message = "\n".join([
        "A client communication has been submitted.",
        "",
        f"Client: {communication.client.client_name}",
        f"Type: {communication.message_type}",
        f"Subject: {communication.subject}",
        f"Location: {communication.location or '-'}",
        f"Description: {communication.description}",
        "",
        f"Review it here: {review_url}",
    ])

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [supervisor.email],
        fail_silently=False,
    )
    return True, f"Notification sent to {supervisor.full_name}."


@login_required
def client_communication_list(request):
    current_client = get_user_client(request.user)
    supervisor = Supervisor.objects.filter(user=request.user).first()
    communications = ClientCommunication.objects.select_related("client", "review_supervisor")

    if current_client and not request.user.is_superuser:
        communications = communications.filter(client=current_client)
    elif supervisor and not request.user.is_superuser:
        communications = communications.filter(review_supervisor=supervisor)

    communications = communications.order_by("-submitted_at", "-communication_id")
    return render(request, "guardmanagementsystem/client_communication_list.html", {
        "communications": communications,
        "self_service_client": current_client,
        "review_supervisor": supervisor,
        "communication_status_choices": ClientCommunication.STATUS_CHOICES,
    })


@login_required
def client_communication_add(request):
    current_client = get_user_client(request.user)
    if not current_client:
        messages.error(request, "Only a linked client user can submit client communication.")
        return redirect("client_communication_list")

    submitted = None
    notification_message = ""
    if request.method == "POST":
        form = ClientCommunicationForm(request.POST, client=current_client)
        if form.is_valid():
            submitted = form.save(commit=False)
            submitted.client = current_client
            submitted.status = "Pending"
            try:
                submitted.full_clean()
                submitted.save()
                log_action(request, "CREATE", f"Submitted client communication {submitted.communication_id}.")
                try:
                    _, notification_message = send_client_communication_notification(request, submitted)
                except Exception as exc:
                    notification_message = f"Request submitted, but email notification could not be sent: {exc}"
                messages.success(request, "Your communication was submitted to the supervisor.")
                return redirect("client_communication_list")
            except ValidationError as exc:
                submitted = None
                form.add_error(None, exc)
    else:
        form = ClientCommunicationForm(client=current_client)

    return render(request, "guardmanagementsystem/client_communication_form.html", {
        "form": form,
        "title": "Submit Client Communication",
        "self_service_client": current_client,
        "notification_message": notification_message,
    })


@login_required
def client_communication_update(request, id):
    communication = get_object_or_404(
        ClientCommunication.objects.select_related("client", "review_supervisor"),
        communication_id=id,
    )

    if request.method != "POST":
        return redirect("client_communication_list")

    supervisor = Supervisor.objects.filter(user=request.user).first()
    if supervisor and communication.review_supervisor_id != supervisor.supervisor_id:
        messages.error(request, "Only the assigned supervisor can update this client communication.")
        return redirect("client_communication_list")

    if not supervisor and not request.user.is_superuser:
        messages.error(request, "Only supervisors can update client communication.")
        return redirect("client_communication_list")

    status = request.POST.get("status")
    if status not in dict(ClientCommunication.STATUS_CHOICES):
        messages.error(request, "Select a valid status.")
        return redirect("client_communication_list")

    communication.status = status
    communication.supervisor_response = str(request.POST.get("supervisor_response") or "").strip()
    communication.reviewed_at = timezone.now()
    communication.save(update_fields=["status", "supervisor_response", "reviewed_at", "review_supervisor"])
    log_action(request, "UPDATE", f"Updated client communication {communication.communication_id}.")
    messages.success(request, "Client communication updated successfully.")
    return redirect("client_communication_list")

@login_required
def deployment_list(request):
    deployments = Deployment.objects.select_related("guard", "client").all()
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
    deployed_guard_ids = Deployment.objects.filter(
        client=client,
        site_location=site_location,
        status="Active",
        start_date__lte=shift_date,
    ).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=shift_date)
    ).values_list("guard_id", flat=True)

    scheduled_shifts = Shift.objects.filter(
        guard_id__in=deployed_guard_ids,
        start_time=shift_date,
    )

    if exclude_guard_ids:
        scheduled_shifts = scheduled_shifts.exclude(guard_id__in=exclude_guard_ids)

    return scheduled_shifts.values("guard_id").distinct().count()


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
                        submitted_deployment.guard,
                        submitted_deployment.client,
                        submitted_deployment.site_location,
                        submitted_deployment.start_date,
                        submitted_deployment.end_date,
                    )
                    deployment_by_guard = {deployment.guard_id: deployment}

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
                    created_attendances = 0
                    skipped_outside_deployment = 0
                    skipped_capacity = 0
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

                            _, attendance_created = Attendance.objects.get_or_create(
                                guard=scheduled_guard,
                                attendance_date=shift_date,
                                defaults={"status": "Present"},
                            )

                            if attendance_created:
                                created_attendances += 1

                            scheduled_today.append(scheduled_guard)
                            scheduled_guard_names.add(scheduled_guard.full_name)

                log_action(request, "CREATE", f"Created scheduled deployment {deployment}.")
                messages.success(
                    request,
                    f"Scheduled {len(scheduled_guard_names)} guard(s) on {deployment.site_location}. "
                    f"Created {created_shifts} shift(s), updated {updated_shifts} shift(s), "
                    f"prepared {created_attendances} attendance record(s), "
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
    return edit_record(request, Deployment, DeploymentForm, {"deployment_id": id}, "Edit Deployment", "deployment_list", "Deployment updated successfully.")


@login_required
def deployment_delete(request, id):
    return delete_record(request, Deployment, {"deployment_id": id}, "Delete Deployment", "deployment_list", "Deployment deleted successfully.")


@login_required
def asset_list(request):
    assets = Asset.objects.select_related("guard").all()
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
def iot_device_list(request):
    devices = IoTDevice.objects.all().order_by("site_location", "device_name")
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
def iot_swipe_attendance(request):
    current_guard = get_user_guard(request.user)
    devices = IoTDevice.objects.filter(is_active=True).order_by("site_location", "device_name")
    site_locations = (
        Deployment.objects.exclude(site_location="")
        .values_list("site_location", flat=True)
        .distinct()
        .order_by("site_location")
    )
    supervisors = Supervisor.objects.order_by("supervisor_number", "full_name")
    rfid_cards = list(RFIDCard.objects.filter(status="Active").order_by("card_number"))
    recent_attendances = recent_attendance_records()
    if current_guard:
        recent_attendances = recent_attendances.filter(
            Q(guard=current_guard) | Q(replacement_guard=current_guard)
        )
    recent_attendances = recent_attendances[:10]
    card_ids = [card.rfid_card_id for card in rfid_cards]
    guard_by_card_id = {
        guard.rfid_card_id: guard
        for guard in Guard.objects.select_related("supervisor").filter(rfid_card_id__in=card_ids)
    }

    for card in rfid_cards:
        assigned_guard = guard_by_card_id.get(card.rfid_card_id)
        supervisor = assigned_guard.supervisor if assigned_guard else None
        card.assigned_supervisor_id = supervisor.supervisor_id if supervisor else ""
        if supervisor:
            guard_label = assigned_guard.guard_number or assigned_guard.full_name
            card.swipe_label = f"{card.card_number} - {guard_label} supervised by {supervisor.full_name}"
        elif assigned_guard:
            card.swipe_label = f"{card.card_number} - Guard {assigned_guard.full_name}"
        else:
            card.swipe_label = f"{card.card_number} - Unassigned"

    for attendance in recent_attendances:
        earning_guard = attendance.replacement_guard or attendance.guard
        supervisor = attendance.swiped_by_supervisor or earning_guard.supervisor
        is_supervisor_login_attendance = (
            supervisor
            and attendance.guard_id == supervisor.guard_id_id
            and attendance.absence_reason == "Supervisor login attendance"
        )

        if supervisor:
            attendance.employee_label = f"{supervisor.supervisor_number or '-'} - {supervisor.full_name}"
        else:
            attendance.employee_label = "-"

        if is_supervisor_login_attendance:
            attendance.guard_profile_label = f"{supervisor.supervisor_number or '-'} - {supervisor.full_name}"
        else:
            attendance.guard_profile_label = f"{earning_guard.guard_number or '-'} - {earning_guard.full_name}"

        deployment = get_matching_deployment(attendance.guard, attendance.attendance_date)
        attendance.client_label = deployment.client.client_name if deployment else "-"
        if is_supervisor_login_attendance:
            attendance.status_label = "Supervisor Login"
        else:
            attendance.status_label = f"Replacement for {attendance.guard.full_name}" if attendance.replacement_guard else attendance.status
        attendance.payroll_guard_id = earning_guard.guard_id
        attendance.payroll_year = attendance.attendance_date.year
        attendance.payroll_month = attendance.attendance_date.month

    if request.method == "POST":
        if current_guard:
            messages.error(request, "Guards can view swipe attendance only.")
            return redirect("iot_swipe_attendance")

        device = IoTDevice.objects.filter(device_id=request.POST.get("device")).first()
        current_site = str(request.POST.get("site_location") or "").strip()
        card_id = request.POST.get("card_id")
        action = request.POST.get("action", "check_in")
        replacement_card_id = request.POST.get("replacement_card_id")
        swiped_by_supervisor_id = request.POST.get("swiped_by_supervisor")
        replacement_guard_id = None

        if not swiped_by_supervisor_id:
            messages.error(request, "Select the supervisor recording this RFID swipe.")
            return redirect("iot_swipe_attendance")

        if action in ["mark_absent", "absent", "replace"] and replacement_card_id == card_id:
            messages.error(request, "Replacement RFID card cannot be the same as the absent assigned RFID card.")
            return redirect("iot_swipe_attendance")

        if replacement_card_id:
            replacement_guard = Guard.objects.filter(
                Q(rfid_card__card_uid=replacement_card_id) |
                Q(rfid_card__card_number=replacement_card_id) |
                Q(rfid_card_number=replacement_card_id)
            ).first()
            replacement_guard_id = replacement_guard.guard_id if replacement_guard else None

        if not device:
            messages.error(request, "Select an active IoT device.")
        else:
            success, message, _ = record_iot_attendance(
                card_id,
                current_site or device.site_location,
                action,
                replacement_guard_id,
                swiped_by_supervisor_id,
            )
            if success:
                messages.success(request, message)
            else:
                messages.error(request, message)

        return redirect("iot_swipe_attendance")

    return render(request, "guardmanagementsystem/iot_swipe_attendance.html", {
        "devices": devices,
        "site_locations": site_locations,
        "supervisors": supervisors,
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
    action = payload.get("action", "check_in")
    supervisor_id = payload.get("supervisor_id") or payload.get("swiped_by_supervisor_id")
    supervisor_number = payload.get("supervisor_number") or payload.get("swiped_by_supervisor_number")
    replacement_guard_id = payload.get("replacement_guard_id")
    replacement_card_id = payload.get("replacement_card_id") or payload.get("replacement_rfid_card_number")

    if supervisor_number and not supervisor_id:
        supervisor = Supervisor.objects.filter(supervisor_number=supervisor_number).first()
        supervisor_id = supervisor.supervisor_id if supervisor else None

    if replacement_card_id and not replacement_guard_id:
        replacement_guard = Guard.objects.filter(
            Q(rfid_card__card_uid=replacement_card_id) |
            Q(rfid_card__card_number=replacement_card_id) |
            Q(rfid_card_number=replacement_card_id)
        ).first()
        replacement_guard_id = replacement_guard.guard_id if replacement_guard else None

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

    site_location = current_site or device.site_location
    success, message, attendance = record_iot_attendance(card_id, site_location, action, replacement_guard_id, supervisor_id)
    data = {
        "success": success,
        "message": message,
        "device": device.device_code,
        "site_location": site_location,
    }

    if attendance:
        deployment = get_matching_deployment(attendance.guard, attendance.attendance_date)
        data.update({
            "guard": attendance.guard.full_name,
            "guard_number": attendance.guard.guard_number,
            "client": deployment.client.client_name if deployment else None,
            "supervisor": attendance.swiped_by_supervisor.full_name if attendance.swiped_by_supervisor else None,
            "supervisor_number": attendance.swiped_by_supervisor.supervisor_number if attendance.swiped_by_supervisor else None,
            "attendance_date": attendance.attendance_date.isoformat(),
            "status": attendance.status,
            "check_in_time": attendance.check_in_time.isoformat() if attendance.check_in_time else None,
            "check_out_time": attendance.check_out_time.isoformat() if attendance.check_out_time else None,
        })

    return JsonResponse(data, status=200 if success else 400)


@login_required
def attendance_list(request):
    attendances = Attendance.objects.select_related("guard").all()
    guards = Guard.objects.all()
    sites = (
        Deployment.objects.exclude(site_location="")
        .values_list("site_location", flat=True)
        .distinct()
        .order_by("site_location")
    )

    site = request.GET.get("site") or request.POST.get("site")
    guard_id = request.GET.get("guard")
    attendance_date = request.GET.get("attendance_date") or request.POST.get("attendance_date")
    status = request.GET.get("status")

    if request.method == "POST":
        if request.POST.get("action") == "save_all_shifts":
            updated_count = 0
            blocked_count = 0

            for attendance_id in request.POST.getlist("attendance_id"):
                attendance = Attendance.objects.filter(attendance_id=attendance_id).first()
                if not attendance:
                    continue

                is_present = request.POST.get(f"present_{attendance_id}") == "yes"
                attendance.status = "Present" if is_present else "Absent"

                if is_present:
                    attendance.replacement_guard = None
                    attendance.absence_reason = ""
                else:
                    replacement_guard_id = request.POST.get(f"replacement_guard_{attendance_id}")
                    replacement_guard = Guard.objects.filter(guard_id=replacement_guard_id).first() if replacement_guard_id else None
                    absence_reason = request.POST.get(f"absence_reason_{attendance_id}", "").strip()

                    if not replacement_guard and not absence_reason:
                        blocked_count += 1
                        continue

                    if replacement_guard and not has_active_deployment(replacement_guard, attendance.attendance_date):
                        blocked_count += 1
                        continue

                    if replacement_guard and not replacement_guard_is_available(replacement_guard, attendance):
                        blocked_count += 1
                        continue

                    attendance.replacement_guard = replacement_guard
                    attendance.absence_reason = absence_reason

                attendance.save()
                updated_count += 1

            messages.success(request, f"Saved {updated_count} attendance shift record(s).")
            if blocked_count:
                messages.warning(request, f"Blocked {blocked_count} attendance record(s) because replacement/absence controls were not satisfied.")
            return redirect(request.POST.get("next") or "attendance_list")

        if request.POST.get("action") == "update_presence":
            attendance = get_object_or_404(Attendance, attendance_id=request.POST.get("attendance_id"))
            is_present = request.POST.get("present") == "yes"
            attendance.status = "Present" if is_present else "Absent"

            if is_present:
                attendance.replacement_guard = None
                attendance.absence_reason = ""
            else:
                replacement_guard_id = request.POST.get("replacement_guard")
                replacement_guard = Guard.objects.filter(guard_id=replacement_guard_id).first() if replacement_guard_id else None
                absence_reason = request.POST.get("absence_reason", "").strip()

                if not replacement_guard and not absence_reason:
                    messages.error(request, "Enter an absence reason or select a replacement guard.")
                    return redirect(request.POST.get("next") or "attendance_list")

                if replacement_guard and not has_active_deployment(replacement_guard, attendance.attendance_date):
                    messages.error(request, "Replacement guard is not actively scheduled on any client site for this date.")
                    return redirect(request.POST.get("next") or "attendance_list")

                if replacement_guard and not replacement_guard_is_available(replacement_guard, attendance):
                    messages.error(request, "Replacement guard is already scheduled or replacing another guard on this date.")
                    return redirect(request.POST.get("next") or "attendance_list")

                attendance.replacement_guard = replacement_guard
                attendance.absence_reason = absence_reason

            attendance.save()
            messages.success(request, "Attendance presence updated.")
            return redirect(request.POST.get("next") or "attendance_list")

        marked_date = parse_date(attendance_date or "")

        if not site or not marked_date:
            messages.error(request, "Select a site and date before marking attendance.")
        else:
            deployments = Deployment.objects.select_related("guard").filter(
                site_location=site,
                status="Active",
                start_date__lte=marked_date,
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=marked_date)
            )

            created_count = 0
            existing_count = 0

            for deployment in deployments:
                _, created = Attendance.objects.get_or_create(
                    guard=deployment.guard,
                    attendance_date=marked_date,
                    defaults={"status": "Present"},
                )

                if created:
                    created_count += 1
                else:
                    existing_count += 1

            if created_count:
                messages.success(request, f"Marked attendance for {created_count} scheduled guard(s).")
            elif existing_count:
                messages.info(request, "Attendance was already marked for the scheduled guard(s).")
            else:
                messages.warning(request, "No active deployments found for that site and date.")

        query = urlencode({
            "site": site or "",
            "attendance_date": attendance_date or "",
        })
        return redirect(f"{request.path}?{query}")

    marked_date = parse_date(attendance_date or "")
    if site and marked_date:
        deployed_guard_ids = Deployment.objects.filter(
            site_location=site,
            status="Active",
            start_date__lte=marked_date,
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=marked_date)
        ).values_list("guard_id", flat=True)
        attendances = attendances.filter(guard_id__in=deployed_guard_ids, attendance_date=marked_date)
    else:
        attendances = attendances.none()

    if guard_id:
        attendances = attendances.filter(guard_id=guard_id)

    if status:
        attendances = attendances.filter(status=status)

    attendance_list_rows = list(attendances.order_by("-attendance_date", "guard__full_name"))
    shift_by_guard_date = {
        (shift.guard_id, shift.start_time): shift
        for shift in Shift.objects.filter(
            guard_id__in=[attendance.guard_id for attendance in attendance_list_rows]
        )
    }

    for attendance in attendance_list_rows:
        shift = shift_by_guard_date.get((attendance.guard_id, attendance.attendance_date))
        attendance.shift_code = get_shift_code(shift)

    return render(request, "guardmanagementsystem/attendance_list.html", {
        "attendances": attendance_list_rows,
        "guards": guards,
        "sites": sites,
        "selected_site": site,
        "selected_guard": guard_id,
        "selected_date": attendance_date,
        "selected_status": status,
        "status_choices": Attendance.STATUS_CHOICES,
    })


@login_required
def attendance_add(request):
    return add_record(request, AttendanceForm, "Add Attendance", "attendance_list", "Attendance added successfully.")


@login_required
def attendance_edit(request, id):
    attendance = get_object_or_404(Attendance, attendance_id=id)

    if request.method == "POST":
        old_guard = attendance.guard
        old_date = attendance.attendance_date
        form = AttendanceForm(request.POST, instance=attendance)

        if form.is_valid():
            updated_attendance = form.save()
            if old_guard != updated_attendance.guard or old_date != updated_attendance.attendance_date:
                recompute_guard_salary_for_month(old_guard, old_date.year, old_date.month)
            messages.success(request, "Attendance updated successfully.")
            return redirect("attendance_list")
    else:
        form = AttendanceForm(instance=attendance)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Attendance"
    })


@login_required
def attendance_delete(request, id):
    return delete_record(request, Attendance, {"attendance_id": id}, "Delete Attendance", "attendance_list", "Attendance deleted successfully.")


@login_required
def shift_list(request):
    shifts = list(Shift.objects.select_related("guard").all())

    for shift in shifts:
        shift.shift_code = get_shift_code(shift)

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
    current_guard = get_user_guard(request.user) if user_is_guard(request.user) else None
    if current_guard:
        selected_guard = str(current_guard.guard_id)
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


def get_salary_advance_deduction(salary):
    if salary.employee_type != "Guard" or not salary.guard:
        return money(0)

    month_number = MONTH_NUMBERS.get(salary.month)
    if not month_number:
        return money(0)

    salary_period_end = date(salary.year, month_number, monthrange(salary.year, month_number)[1])
    total = AdvanceRequest.objects.filter(
        guard=salary.guard,
        status__in=["Approved", "Paid"],
        request_date__lte=salary_period_end,
    ).aggregate(total=Sum("amount"))["total"]

    return money(total)


def build_salary_payroll_context(salary):
    gross_pay = money(salary.basic_pay) + money(salary.allowances)
    paye = calculate_uganda_paye(gross_pay)
    nssf_employee = money(gross_pay * Decimal("0.05"))
    nssf_employer = money(gross_pay * Decimal("0.10"))
    lst_annual = calculate_local_service_tax_annual(gross_pay)
    month_number = MONTH_NUMBERS.get(salary.month, 0)
    lst_deduction = money(lst_annual / Decimal("4")) if month_number in [7, 8, 9, 10] else money(0)
    advance_total = get_salary_advance_deduction(salary)
    advance_installment_limit = money(gross_pay * Decimal("0.20"))
    manual_deductions = money(salary.deductions)
    statutory_deductions = paye + nssf_employee + lst_deduction
    available_for_advance = max(gross_pay - statutory_deductions - manual_deductions, money(0))
    advance_deduction = money(min(advance_total, advance_installment_limit, available_for_advance))
    advance_balance = money(advance_total - advance_deduction)
    total_deductions = statutory_deductions + manual_deductions + advance_deduction
    net_payable = gross_pay - total_deductions

    return {
        "gross_pay": money(gross_pay),
        "paye": paye,
        "nssf_employee": nssf_employee,
        "nssf_employer": nssf_employer,
        "lst_annual": lst_annual,
        "lst_deduction": lst_deduction,
        "advance_total": advance_total,
        "advance_installment_limit": advance_installment_limit,
        "advance_deduction": advance_deduction,
        "advance_balance": advance_balance,
        "manual_deductions": manual_deductions,
        "statutory_deductions": money(statutory_deductions),
        "total_deductions": money(total_deductions),
        "net_payable": money(net_payable),
    }


@login_required
def salary_payslip_individual(request, id):
    salary = get_object_or_404(
        Salary.objects.select_related("guard", "supervisor"),
        salary_id=id
    )
    return render(request, "guardmanagementsystem/payslip.html", {
        "salary": salary,
        "payroll": build_salary_payroll_context(salary),
        "title": "Individual Payslip",
    })


@login_required
def salary_payslip_from_attendance(request, guard_id, year, month):
    current_guard = get_user_guard(request.user) if user_is_guard(request.user) else None
    if current_guard and current_guard.guard_id != guard_id:
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
        "advance_deduction": money(sum(row["payroll"]["advance_deduction"] for row in payroll_rows)),
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
        supervisor_id = request.GET.get("supervisor")

        if guard_id:
            initial = {
                "employee_type": "Guard",
                "guard": guard_id,
            }
        elif supervisor_id:
            initial = {
                "employee_type": "Supervisor",
                "supervisor": supervisor_id,
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
    incidents = Incident.objects.select_related("guard", "reported_by").all()
    return render(request, "guardmanagementsystem/incident_list.html", {
        "incidents": incidents
    })


@login_required
def incident_pdf_report(request, id):
    incident = get_object_or_404(
        Incident.objects.select_related("guard", "reported_by"),
        incident_id=id
    )
    incident_time = incident.incident_time.strftime("%I:%M %p") if incident.incident_time else "-"
    supervisor = incident.reported_by
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
                ("Severity / Priority", "To be assessed by supervisor"),
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
                ("Supervisor", supervisor.full_name if supervisor else "-"),
                ("Supervisor Phone", supervisor.phone if supervisor else "-"),
                ("Supervisor Email", supervisor.email if supervisor else "-"),
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
                ("Notification Made To", supervisor.full_name if supervisor else "-"),
                ("Police / External Reference", "Not recorded"),
            ]),
            ("Evidence and Attachments", [
                ("Photos / CCTV / Documents", "Not recorded"),
                ("Assets Affected", "Not recorded"),
            ]),
            ("Follow Up and Closure", [
                ("Corrective Action Required", "To be completed by supervisor"),
                ("Responsible Person", supervisor.full_name if supervisor else "-"),
                ("Closure Notes", "To be completed after review"),
            ]),
        ]
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="incident_report_IR-{incident.incident_id}.pdf"'
    return response


@login_required
def incident_add(request):
    supervisor = get_user_supervisor(request.user)
    initial = {
        "incident_date": timezone.localdate(),
        "incident_time": timezone.localtime().time().replace(second=0, microsecond=0),
        "status": "Pending",
    }
    if supervisor:
        initial["reported_by"] = supervisor

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


@login_required
def disciplinary_action_list(request):
    disciplinary_actions = DisciplinaryAction.objects.select_related(
        "guard", "issued_by"
    ).all()
    return show_records(
        request,
        disciplinary_actions,
        "guardmanagementsystem/disciplinary_action_list.html",
        "disciplinary_actions"
    )


@login_required
def disciplinary_action_add(request):
    return add_record(
        request,
        DisciplinaryActionForm,
        "Add Disciplinary Action",
        "disciplinary_action_list",
        "Disciplinary action added successfully."
    )


@login_required
def disciplinary_action_edit(request, id):
    return edit_record(
        request,
        DisciplinaryAction,
        DisciplinaryActionForm,
        {"disciplinary_id": id},
        "Edit Disciplinary Action",
        "disciplinary_action_list",
        "Disciplinary action updated successfully."
    )


@login_required
def disciplinary_action_delete(request, id):
    return delete_record(
        request,
        DisciplinaryAction,
        {"disciplinary_id": id},
        "Delete Disciplinary Action",
        "disciplinary_action_list",
        "Disciplinary action deleted successfully."
    )


def get_advance_review_supervisor(request, advance_request):
    assigned_supervisor = advance_request.review_supervisor or advance_request.guard.supervisor
    user_supervisor = Supervisor.objects.filter(user=request.user).first() if request.user.is_authenticated else None

    if user_supervisor:
        return user_supervisor if user_supervisor == assigned_supervisor else None

    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return assigned_supervisor

    return None


def send_advance_request_notification(request, advance_request):
    supervisor = advance_request.review_supervisor or advance_request.guard.supervisor
    if not supervisor or not supervisor.email:
        return False, "No supervisor email is configured for this guard."

    review_url = request.build_absolute_uri("/advance-requests/")
    subject = f"Advance request from {advance_request.guard.full_name}"
    message = "\n".join([
        "A guard has submitted a remote salary advance request.",
        "",
        f"Guard: {advance_request.guard.guard_number} - {advance_request.guard.full_name}",
        f"Phone: {advance_request.guard.phone}",
        f"Amount: {advance_request.amount}",
        f"Monthly installment: {advance_request.installment_amount}",
        f"Recovery period: {advance_request.recovery_period_months} months",
        f"Reason: {advance_request.reason}",
        f"Status: {advance_request.status}",
        "",
        f"Review the request here: {review_url}",
    ])

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [supervisor.email],
        fail_silently=False,
    )
    return True, f"Notification sent to {supervisor.full_name}."


def advance_request_remote(request):
    submitted = None
    notification_message = ""
    current_guard = get_user_guard(request.user) if user_is_guard(request.user) else None

    if request.method == "POST":
        form = GuardSelfAdvanceRequestForm(request.POST) if current_guard else RemoteAdvanceRequestForm(request.POST)
        if form.is_valid():
            guard = current_guard or form.cleaned_data["guard"]
            submitted = AdvanceRequest(
                guard=guard,
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                status="Pending",
            )
            try:
                submitted.full_clean()
                submitted.save()
                try:
                    _, notification_message = send_advance_request_notification(request, submitted)
                except Exception as exc:
                    notification_message = f"Request submitted, but notification could not be sent: {exc}"
            except ValidationError as exc:
                submitted = None
                form.add_error(None, exc)
    else:
        form = GuardSelfAdvanceRequestForm() if current_guard else RemoteAdvanceRequestForm()

    return render(request, "guardmanagementsystem/advance_request_remote.html", {
        "form": form,
        "submitted": submitted,
        "notification_message": notification_message,
        "self_service_guard": current_guard,
    })


@login_required
def advance_request_list(request):
    advance_queryset = AdvanceRequest.objects.select_related(
        "guard", "guard__supervisor", "review_supervisor", "approved_by"
    )
    supervisor = Supervisor.objects.filter(user=request.user).first()
    if supervisor and not request.user.is_superuser:
        advance_queryset = advance_queryset.filter(review_supervisor=supervisor)

    advance_requests = list(advance_queryset.order_by("-request_date", "-advance_id"))

    for advance_request in advance_requests:
        advance_request.display_installment_amount = (
            advance_request.installment_amount
            if advance_request.installment_amount
            else advance_request.calculate_installment_amount()
        )
        advance_request.display_review_supervisor = advance_request.review_supervisor or advance_request.guard.supervisor

    return render(request, "guardmanagementsystem/advance_request_list.html", {
        "advance_requests": advance_requests
    })


@login_required
def advance_request_add(request):
    return add_record(request, AdvanceRequestForm, "Add Advance Request", "advance_request_list", "Advance request added successfully.")


@login_required
def advance_request_edit(request, id):
    return edit_record(request, AdvanceRequest, AdvanceRequestForm, {"advance_id": id}, "Edit Advance Request", "advance_request_list", "Advance request updated successfully.")


@login_required
def advance_request_delete(request, id):
    return delete_record(request, AdvanceRequest, {"advance_id": id}, "Delete Advance Request", "advance_request_list", "Advance request deleted successfully.")


@login_required
def advance_request_decision(request, id, decision):
    advance_request = get_object_or_404(
        AdvanceRequest.objects.select_related("guard", "guard__supervisor"),
        advance_id=id,
    )

    if request.method != "POST":
        return redirect("advance_request_list")

    if decision not in ["approve", "reject"]:
        messages.error(request, "Invalid advance request decision.")
        return redirect("advance_request_list")

    supervisor = get_advance_review_supervisor(request, advance_request)
    if not supervisor:
        messages.error(request, "Only the assigned supervisor can approve or reject this request.")
        return redirect("advance_request_list")

    if advance_request.status != "Pending":
        messages.warning(request, "Only pending advance requests can be approved or rejected.")
        return redirect("advance_request_list")

    rejection_reason = str(request.POST.get("rejection_reason") or "").strip()
    approval_reason = str(request.POST.get("approval_reason") or "").strip()
    if decision == "approve" and not approval_reason:
        messages.error(request, "Enter a reason for approving this advance request.")
        return redirect("advance_request_list")

    if decision == "reject" and not rejection_reason:
        messages.error(request, "Enter a reason for rejecting this advance request.")
        return redirect("advance_request_list")

    advance_request.status = "Approved" if decision == "approve" else "Rejected"
    advance_request.approved_by = supervisor
    advance_request.approved_date = timezone.localdate()
    advance_request.approval_reason = approval_reason if decision == "approve" else ""
    advance_request.rejection_reason = rejection_reason if decision == "reject" else ""
    advance_request.save(update_fields=["status", "approved_by", "approved_date", "approval_reason", "rejection_reason", "installment_amount", "recovery_period_months", "review_supervisor"])

    log_action(request, "UPDATE", f"{advance_request.status} advance request {advance_request.advance_id}.")
    messages.success(request, f"Advance request {advance_request.status.lower()} successfully.")
    return redirect("advance_request_list")
