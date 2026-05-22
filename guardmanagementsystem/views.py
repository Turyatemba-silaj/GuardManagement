from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages 
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Count, Q
from django.db.models.functions import ExtractMonth
from django.utils import timezone
from django.utils.dateparse import parse_date
from urllib.parse import urlencode
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import timedelta


from .models import *
from .forms import *


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


def replacement_guard_is_available(replacement_guard, attendance):
    if not replacement_guard or not attendance:
        return True

    if replacement_guard == attendance.guard:
        return False

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
                "shift_name": f"Imported Roster ({shift_code.upper()})",
                "attendance_status": "Present",
            })
            expanded_rows.append(roster_row)

    return expanded_rows


def shift_import(request):
    if request.method == "POST":
        roster_file = request.FILES.get("roster_file")

        if not roster_file:
            messages.error(request, "Choose a roster file to import.")
            return redirect("shift_import")

        try:
            sheet_rows = read_roster_sheet(roster_file)
        except (ValueError, zipfile.BadZipFile, ET.ParseError) as error:
            messages.error(request, str(error))
            return redirect("shift_import")

        rows = expand_wide_roster_rows(sheet_rows) or rows_to_dicts(sheet_rows)
        created_count = 0
        attendance_created_count = 0
        attendance_existing_count = 0
        skipped_rows = []

        for row_number, row in enumerate(rows, start=2):
            normalized_row = {normalize_header(key): value for key, value in row.items()}
            employee_number = str(get_row_value(normalized_row, "employee_number", "employee_no", "emp_no", "emp_number")).strip()
            shift_date_value = get_row_value(normalized_row, "shift_date", "date", "date_scheduled", "start_date")
            shift_type_value = get_row_value(normalized_row, "shift_type", "shift", "d_n", "d_or_n")
            shift_name = str(get_row_value(normalized_row, "shift_name", "name") or "Imported Shift").strip()
            attendance_status_value = get_row_value(normalized_row, "attendance_status", "status", "attendance", "present")
            absence_reason = str(get_row_value(normalized_row, "absence_reason", "reason") or "").strip()

            guard = find_guard_for_roster_row(normalized_row)
            shift_date = parse_excel_serial_date(shift_date_value)

            if not guard or not shift_date:
                skipped_rows.append(row_number)
                continue

            if not has_active_deployment(guard, shift_date):
                skipped_rows.append(row_number)
                continue

            shift_type = normalize_shift_type(shift_type_value)
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
            messages.success(request, f"Imported {created_count} duty roster shift(s).")
        else:
            messages.info(request, "No new duty roster shifts were imported.")

        if attendance_created_count:
            messages.success(request, f"Added {attendance_created_count} uploaded row(s) to Attendance.")
        elif attendance_existing_count:
            messages.info(request, "Uploaded rows were already available in Attendance.")

        if skipped_rows:
            messages.warning(request, f"Skipped row(s): {', '.join(str(row) for row in skipped_rows)}.")

        return redirect("attendance_list")

    return render(request, "guardmanagementsystem/shift_import.html")


def schedule_report(request):
    deployment_area = request.GET.get("deployment_area", "")
    site = request.GET.get("site", "")
    start_date = request.GET.get("start_date", "")
    end_date = request.GET.get("end_date", "")

    deployments = Deployment.objects.select_related("client", "guard").all()
    clients = Client.objects.all().order_by("client_name")
    sites = (
        Deployment.objects.exclude(site_location="")
        .values_list("site_location", flat=True)
        .distinct()
        .order_by("site_location")
    )

    if deployment_area:
        deployments = deployments.filter(client_id=deployment_area)

    if site:
        deployments = deployments.filter(site_location=site)

    deployment_guard_ids = list(deployments.values_list("guard_id", flat=True))
    shifts = Shift.objects.select_related("guard").filter(guard_id__in=deployment_guard_ids)

    if start_date:
        shifts = shifts.filter(start_time__gte=start_date)

    if end_date:
        shifts = shifts.filter(start_time__lte=end_date)

    deployment_by_guard = {}
    for deployment in deployments:
        deployment_by_guard.setdefault(deployment.guard_id, deployment)

    report_index = {}
    for shift in shifts:
        deployment = deployment_by_guard.get(shift.guard_id)
        if not deployment:
            continue

        key = (shift.start_time, deployment.site_location)
        row = report_index.setdefault(key, {
            "date_scheduled": shift.start_time,
            "site_scheduled": deployment.site_location,
            "day_shift": 0,
            "night_shift": 0,
        })

        shift_code = get_shift_code(shift)

        if shift_code == "N":
            row["night_shift"] += 1
        elif shift_code == "D/N":
            row["day_shift"] += 1
            row["night_shift"] += 1
        else:
            row["day_shift"] += 1

    report_rows = sorted(
        report_index.values(),
        key=lambda row: (row["date_scheduled"], row["site_scheduled"])
    )

    return render(request, "guardmanagementsystem/schedule_report.html", {
        "clients": clients,
        "sites": sites,
        "report_rows": report_rows,
        "selected_deployment_area": deployment_area,
        "selected_site": site,
        "selected_start_date": start_date,
        "selected_end_date": end_date,
    })


def attendance_query_report(request):
    employee_number = request.GET.get("employee_number", "")
    start_date = request.GET.get("start_date", "")
    end_date = request.GET.get("end_date", "")

    attendances = Attendance.objects.select_related("guard").all()

    if employee_number:
        attendances = attendances.filter(
            Q(guard__guard_id__icontains=employee_number) |
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
    attendance_counts = dict(
        Attendance.objects.filter(attendance_date__year=current_year)
        .annotate(month=ExtractMonth("attendance_date"))
        .values("month")
        .annotate(total=Count("attendance_id"))
        .values_list("month", "total")
    )

    total_supervisors = Supervisor.objects.count()
    total_guards = Guard.objects.count()
    total_clients = Client.objects.count()
    total_deployments = Deployment.objects.count()
    total_assets = Asset.objects.count()
    total_attendance = Attendance.objects.count()
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
        "total_attendance": total_attendance,
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
            "Attendance",
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
            total_attendance,
            total_shifts,
            total_salaries,
            total_incidents,
            total_disciplinary_actions,
            total_advance_requests,
        ],
        "month_labels": month_labels,
        "monthly_incidents": [incident_counts.get(month, 0) for month in range(1, 13)],
        "monthly_attendance": [attendance_counts.get(month, 0) for month in range(1, 13)],
    }

    return render(request, "guardmanagementsystem/dashboard.html", context)

    

def guard_list(request):
    guards = Guard.objects.all()
    return render(request, "guardmanagementsystem/guard_list.html", {
        "guards": guards
    })


def guard_add(request):
    if request.method == "POST":
        form = GuardForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Guard added successfully")
            return redirect("guard_list")
    else:
        form = GuardForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Guard"
    })


def guard_edit(request, id):
    guard = get_object_or_404(Guard, guard_id=id)

    if request.method == "POST":
        form = GuardForm(request.POST, instance=guard)

        if form.is_valid():
            form.save()
            messages.success(request, "Guard updated successfully")
            return redirect("guard_list")
    else:
        form = GuardForm(instance=guard)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Guard"
    })


def guard_delete(request, id):
    guard = get_object_or_404(Guard, guard_id=id)

    if request.method == "POST":
        guard.delete()
        messages.success(request, "Guard deleted successfully")
        return redirect("guard_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": guard,
        "title": "Delete Guard"
    })


def supervisor_list(request):
    supervisors = Supervisor.objects.all()
    return render(request, "guardmanagementsystem/supervisor_list.html", {
        "supervisors": supervisors
    })


def supervisor_add(request):
    if request.method == "POST":
        form = SupervisorForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Supervisor added successfully")
            return redirect("supervisor_list")
    else:
        form = SupervisorForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Supervisor"
    })


def supervisor_edit(request, id):
    supervisor = get_object_or_404(Supervisor, supervisor_id=id)

    if request.method == "POST":
        form = SupervisorForm(request.POST, instance=supervisor)

        if form.is_valid():
            form.save()
            messages.success(request, "Supervisor updated successfully")
            return redirect("supervisor_list")
    else:
        form = SupervisorForm(instance=supervisor)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Supervisor"
    })


def supervisor_delete(request, id):
    supervisor = get_object_or_404(Supervisor, supervisor_id=id)

    if request.method == "POST":
        supervisor.delete()
        messages.success(request, "Supervisor deleted successfully")
        return redirect("supervisor_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": supervisor,
        "title": "Delete Supervisor"
    })

def client_list(request):
    clients = Client.objects.all()
    return render(request, "guardmanagementsystem/client_list.html", {
        "clients": clients
    })


def client_add(request):
    if request.method == "POST":
        form = ClientForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Client added successfully")
            return redirect("client_list")
    else:
        form = ClientForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Client"
    })


def client_edit(request, id):
    client = get_object_or_404(Client, client_id=id)

    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)

        if form.is_valid():
            form.save()
            messages.success(request, "Client updated successfully")
            return redirect("client_list")
    else:
        form = ClientForm(instance=client)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Client"
    })


def client_delete(request, id):
    client = get_object_or_404(Client, client_id=id)

    if request.method == "POST":
        client.delete()
        messages.success(request, "Client deleted successfully")
        return redirect("client_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": client,
        "title": "Delete Client"
    })

def deployment_list(request):
    deployments = Deployment.objects.select_related("guard", "client").all()
    return render(request, "guardmanagementsystem/deployment_list.html", {
        "deployments": deployments
    })


def deployment_add(request):
    if request.method == "POST":
        form = DeploymentForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Deployment added successfully.")
            return redirect("deployment_list")
    else:
        form = DeploymentForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Deployment"
    })


def program_guard(request):
    if request.method == "POST":
        form = ProgramGuardForm(request.POST)

        if form.is_valid():
            deployment = form.save(commit=False)
            deployment.status = "Active"
            deployment.save()
            messages.success(
                request,
                f"{deployment.guard.full_name} has been programmed on {deployment.site_location}."
            )
            return redirect("deployment_list")
    else:
        form = ProgramGuardForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Program Guard on Site"
    })


def deployment_edit(request, id):
    deployment = get_object_or_404(Deployment, deployment_id=id)

    if request.method == "POST":
        form = DeploymentForm(request.POST, instance=deployment)

        if form.is_valid():
            form.save()
            messages.success(request, "Deployment updated successfully.")
            return redirect("deployment_list")
    else:
        form = DeploymentForm(instance=deployment)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Deployment"
    })


def deployment_delete(request, id):
    deployment = get_object_or_404(Deployment, deployment_id=id)

    if request.method == "POST":
        deployment.delete()
        messages.success(request, "Deployment deleted successfully.")
        return redirect("deployment_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": deployment,
        "title": "Delete Deployment"
    })
def asset_list(request):
    assets = Asset.objects.select_related("guard").all()
    return render(request, "guardmanagementsystem/asset_list.html", {
        "assets": assets
    })


def asset_add(request):
    if request.method == "POST":
        form = AssetForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Asset added successfully.")
            return redirect("asset_list")
    else:
        form = AssetForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Asset"
    })


def asset_edit(request, id):
    asset = get_object_or_404(Asset, asset_id=id)

    if request.method == "POST":
        form = AssetForm(request.POST, instance=asset)

        if form.is_valid():
            form.save()
            messages.success(request, "Asset updated successfully.")
            return redirect("asset_list")
    else:
        form = AssetForm(instance=asset)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Asset"
    })


def asset_delete(request, id):
    asset = get_object_or_404(Asset, asset_id=id)

    if request.method == "POST":
        asset.delete()
        messages.success(request, "Asset deleted successfully.")
        return redirect("asset_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": asset,
        "title": "Delete Asset"
    })

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
                    messages.error(request, "Replacement guard is not actively programmed on any client site for this date.")
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

    if guard_id:
        attendances = attendances.filter(guard_id=guard_id)

    if site:
        deployed_guard_ids = Deployment.objects.filter(site_location=site).values_list("guard_id", flat=True)
        attendances = attendances.filter(guard_id__in=deployed_guard_ids)

    if attendance_date:
        attendances = attendances.filter(attendance_date=attendance_date)

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


def attendance_add(request):
    if request.method == "POST":
        form = AttendanceForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Attendance added successfully.")
            return redirect("attendance_list")
    else:
        form = AttendanceForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Attendance"
    })


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


def attendance_delete(request, id):
    attendance = get_object_or_404(Attendance, attendance_id=id)

    if request.method == "POST":
        attendance.delete()
        messages.success(request, "Attendance deleted successfully.")
        return redirect("attendance_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": attendance,
        "title": "Delete Attendance"
    })
def shift_list(request):
    shifts = list(Shift.objects.select_related("guard").all())

    for shift in shifts:
        shift.shift_code = get_shift_code(shift)

    return render(request, "guardmanagementsystem/shift_list.html", {
        "shifts": shifts
    })


def shift_add(request):
    if request.method == "POST":
        form = ShiftForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Shift added successfully.")
            return redirect("shift_list")
    else:
        form = ShiftForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Shift"
    })


def shift_edit(request, id):
    shift = get_object_or_404(Shift, shift_id=id)

    if request.method == "POST":
        form = ShiftForm(request.POST, instance=shift)

        if form.is_valid():
            form.save()
            messages.success(request, "Shift updated successfully.")
            return redirect("shift_list")
    else:
        form = ShiftForm(instance=shift)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Shift"
    })


def shift_delete(request, id):
    shift = get_object_or_404(Shift, shift_id=id)

    if request.method == "POST":
        shift.delete()
        messages.success(request, "Shift deleted successfully.")
        return redirect("shift_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": shift,
        "title": "Delete Shift"
    })

def salary_list(request):
    salaries = Salary.objects.select_related("guard", "supervisor").all()
    return render(request, "guardmanagementsystem/salary_list.html", {
        "salaries": salaries
    })


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


def salary_edit(request, id):
    salary = get_object_or_404(Salary, salary_id=id)

    if request.method == "POST":
        form = SalaryForm(request.POST, instance=salary)

        if form.is_valid():
            form.save()
            messages.success(request, "Salary updated successfully.")
            return redirect("salary_list")
    else:
        form = SalaryForm(instance=salary)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Salary"
    })


def salary_delete(request, id):
    salary = get_object_or_404(Salary, salary_id=id)

    if request.method == "POST":
        salary.delete()
        messages.success(request, "Salary deleted successfully.")
        return redirect("salary_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": salary,
        "title": "Delete Salary"
    })

def incident_list(request):
    incidents = Incident.objects.select_related("guard", "reported_by").all()
    return render(request, "guardmanagementsystem/incident_list.html", {
        "incidents": incidents
    })


def incident_add(request):
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
        form = IncidentForm()

    return render(request, "guardmanagementsystem/incident_form.html", {
        "form": form,
        "title": "Incident Report Form",
        "button_text": "Report Now!"
    })


def incident_edit(request, id):
    incident = get_object_or_404(Incident, incident_id=id)

    if request.method == "POST":
        form = IncidentForm(request.POST, instance=incident)

        if form.is_valid():
            form.save()
            messages.success(request, "Incident updated successfully.")
            return redirect("incident_list")
    else:
        form = IncidentForm(instance=incident)

    return render(request, "guardmanagementsystem/incident_form.html", {
        "form": form,
        "title": "Edit Incident Report",
        "button_text": "Report Now!"
    })


def incident_delete(request, id):
    incident = get_object_or_404(Incident, incident_id=id)

    if request.method == "POST":
        incident.delete()
        messages.success(request, "Incident deleted successfully.")
        return redirect("incident_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": incident,
        "title": "Delete Incident"
    })

def disciplinary_action_list(request):
    disciplinary_actions = DisciplinaryAction.objects.select_related(
        "guard", "issued_by"
    ).all()

    return render(request, "guardmanagementsystem/disciplinary_action_list.html", {
        "disciplinary_actions": disciplinary_actions
    })


def disciplinary_action_add(request):
    if request.method == "POST":
        form = DisciplinaryActionForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Disciplinary action added successfully.")
            return redirect("disciplinary_action_list")
    else:
        form = DisciplinaryActionForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Disciplinary Action"
    })


def disciplinary_action_edit(request, id):
    disciplinary_action = get_object_or_404(
        DisciplinaryAction,
        disciplinary_id=id
    )

    if request.method == "POST":
        form = DisciplinaryActionForm(
            request.POST,
            instance=disciplinary_action
        )

        if form.is_valid():
            form.save()
            messages.success(request, "Disciplinary action updated successfully.")
            return redirect("disciplinary_action_list")
    else:
        form = DisciplinaryActionForm(instance=disciplinary_action)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Disciplinary Action"
    })


def disciplinary_action_delete(request, id):
    disciplinary_action = get_object_or_404(
        DisciplinaryAction,
        disciplinary_id=id
    )

    if request.method == "POST":
        disciplinary_action.delete()
        messages.success(request, "Disciplinary action deleted successfully.")
        return redirect("disciplinary_action_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": disciplinary_action,
        "title": "Delete Disciplinary Action"
    })


def advance_request_list(request):
    advance_requests = AdvanceRequest.objects.select_related(
        "guard", "approved_by"
    ).all()

    return render(request, "guardmanagementsystem/advance_request_list.html", {
        "advance_requests": advance_requests
    })


def advance_request_add(request):
    if request.method == "POST":
        form = AdvanceRequestForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(request, "Advance request added successfully.")
            return redirect("advance_request_list")
    else:
        form = AdvanceRequestForm()

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Add Advance Request"
    })


def advance_request_edit(request, id):
    advance_request = get_object_or_404(AdvanceRequest, advance_id=id)

    if request.method == "POST":
        form = AdvanceRequestForm(request.POST, instance=advance_request)

        if form.is_valid():
            form.save()
            messages.success(request, "Advance request updated successfully.")
            return redirect("advance_request_list")
    else:
        form = AdvanceRequestForm(instance=advance_request)

    return render(request, "guardmanagementsystem/form.html", {
        "form": form,
        "title": "Edit Advance Request"
    })


def advance_request_delete(request, id):
    advance_request = get_object_or_404(AdvanceRequest, advance_id=id)

    if request.method == "POST":
        advance_request.delete()
        messages.success(request, "Advance request deleted successfully.")
        return redirect("advance_request_list")

    return render(request, "guardmanagementsystem/delete.html", {
        "object": advance_request,
        "title": "Delete Advance Request"
    })
