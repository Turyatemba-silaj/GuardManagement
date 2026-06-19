PUBLIC_URL_NAMES = {
    "welcome",
    "signin",
    "signout",
    "signup",
    "forgot_password",
    "reset_password",
    "advance_request_remote",
    "iot_attendance_api",
}

ADMIN_URLS = {
    "user_list",
    "user_add",
    "user_edit",
    "user_delete",
    "audit_log_list",
}

OPERATIONS_URLS = {
    "client_list",
    "client_add",
    "client_edit",
    "client_delete",
    "deployment_list",
    "deployment_add",
    "deployment_edit",
    "deployment_delete",
    "program_guard",
    "shift_list",
    "shift_add",
    "shift_edit",
    "shift_delete",
    "shift_import",
    "schedule_csv_template",
    "incident_list",
    "incident_add",
    "incident_edit",
    "incident_delete",
    "incident_pdf_report",
    "attendance_list",
    "attendance_add",
    "attendance_edit",
    "attendance_delete",
}

HR_URLS = {
    "guard_list",
    "guard_add",
    "guard_edit",
    "guard_delete",
    "supervisor_list",
    "supervisor_add",
    "supervisor_edit",
    "supervisor_delete",
    "disciplinary_action_list",
    "disciplinary_action_add",
    "disciplinary_action_edit",
    "disciplinary_action_delete",
    "salary_list",
    "salary_payslip_general",
    "salary_payslip_from_attendance",
    "salary_payslip_individual",
}

FINANCE_URLS = {
    "salary_list",
    "salary_add",
    "salary_edit",
    "salary_delete",
    "salary_payslip_general",
    "salary_payslip_from_attendance",
    "salary_payslip_individual",
    "advance_request_list",
    "advance_request_add",
    "advance_request_edit",
    "advance_request_delete",
    "advance_request_decision",
}

SUPERVISOR_URLS = {
    "attendance_query_report",
    "schedule_report",
    "schedule_report_csv_export",
    "incident_list",
    "incident_add",
    "incident_pdf_report",
    "iot_swipe_attendance",
    "advance_request_list",
    "advance_request_add",
    "advance_request_edit",
    "advance_request_delete",
    "advance_request_decision",
}

GUARD_URLS = {
    "advance_request_remote",
    "iot_swipe_attendance",
    "salary_list",
    "salary_payslip_from_attendance",
}

CLIENT_URLS = {
    "client_communication_list",
    "client_communication_add",
}

CLIENT_COMMUNICATION_URLS = {
    "client_communication_list",
    "client_communication_add",
    "client_communication_update",
}

DEVICE_REPORT_URLS = {
    "attendance_query_report",
    "schedule_report",
    "schedule_report_csv_export",
    "rfid_card_list",
    "rfid_card_add",
    "rfid_card_edit",
    "rfid_card_delete",
    "iot_device_list",
    "iot_device_add",
    "iot_device_edit",
    "iot_device_delete",
    "iot_swipe_attendance",
}

ROLE_URLS = {
    "Admin": ADMIN_URLS | OPERATIONS_URLS | HR_URLS | FINANCE_URLS | DEVICE_REPORT_URLS | CLIENT_COMMUNICATION_URLS | {"dashboard"},
    "Supervisor": ((
        ADMIN_URLS
        | OPERATIONS_URLS
        | HR_URLS
        | FINANCE_URLS
        | DEVICE_REPORT_URLS
        | CLIENT_COMMUNICATION_URLS
        | {"dashboard"}
    ) - {"client_add", "guard_add", "supervisor_add", "dashboard", "audit_log_list"}),
    "Guard": GUARD_URLS,
    "Client": CLIENT_URLS,
}

ROLE_PERMISSIONS = {
    "can_people": {"Admin", "Supervisor"},
    "can_users": {"Admin", "Supervisor"},
    "can_audit_logs": {"Admin"},
    "can_operations": {"Admin", "Supervisor"},
    "can_payroll_cases": {"Admin", "Supervisor"},
    "can_reports_devices": {"Admin", "Supervisor"},
    "can_salaries": {"Admin", "Supervisor"},
    "can_advances": {"Admin", "Supervisor"},
    "can_cases": {"Admin", "Supervisor"},
    "can_devices": {"Admin", "Supervisor"},
    "can_guard_self_service": {"Guard"},
    "can_client_self_service": {"Client"},
    "can_client_communications": {"Admin", "Supervisor"},
    "can_dashboard": {"Admin"},
    "can_add_people_profiles": {"Admin"},
}


def user_role_names(user):
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {"Admin"}
    return set(user.groups.values_list("name", flat=True))


def user_can_access(user, url_name):
    if not url_name or url_name in PUBLIC_URL_NAMES:
        return True
    if not user.is_authenticated:
        return True
    if user.is_superuser:
        return True

    roles = user_role_names(user)
    return any(url_name in ROLE_URLS.get(role, set()) for role in roles)


def nav_permissions(user):
    roles = user_role_names(user)
    if getattr(user, "is_superuser", False):
        roles = {"Admin"}
    return {
        permission: bool(roles & allowed_roles)
        for permission, allowed_roles in ROLE_PERMISSIONS.items()
    }
