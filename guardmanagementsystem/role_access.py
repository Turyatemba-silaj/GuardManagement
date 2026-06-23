PUBLIC_URL_NAMES = {
    "welcome",
    "signin",
    "signout",
    "signup",
    "forgot_password",
    "reset_password",
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
    "contract_list",
    "contract_add",
    "contract_iot_devices",
    "contract_edit",
    "contract_delete",
    "deployment_list",
    "deployment_add",
    "deployment_edit",
    "deployment_delete",
    "deployment_guard_list",
    "deployment_guard_add",
    "deployment_guard_edit",
    "deployment_guard_delete",
    "program_guard",
    "shift_list",
    "shift_add",
    "shift_edit",
    "shift_delete",
    "shift_import",
    "schedule_csv_template",
    "schedule_xlsx_template",
    "asset_list",
    "asset_add",
    "asset_edit",
    "asset_delete",
    "asset_lifecycle_action",
    "incident_list",
    "incident_add",
    "incident_edit",
    "incident_delete",
    "incident_pdf_report",
}

HR_URLS = {
    "guard_list",
    "guard_add",
    "guard_edit",
    "guard_delete",
    "salary_list",
    "salary_payslip_general",
    "salary_payslip_from_attendance",
    "salary_attendance_summary",
    "salary_payslip_individual",
}

FINANCE_URLS = {
    "salary_list",
    "salary_add",
    "salary_edit",
    "salary_delete",
    "salary_payslip_general",
    "salary_payslip_from_attendance",
    "salary_attendance_summary",
    "salary_payslip_individual",
}

DEVICE_REPORT_URLS = {
    "rfid_card_list",
    "rfid_card_add",
    "rfid_card_edit",
    "rfid_card_delete",
    "iot_device_list",
    "iot_device_add",
    "iot_device_edit",
    "iot_device_delete",
    "iot_swipe_attendance",
    "iot_swipe_options",
}

GUARD_SELF_SERVICE_URLS = {
    "iot_swipe_attendance",
    "iot_swipe_options",
    "salary_list",
    "salary_payslip_from_attendance",
    "salary_attendance_summary",
}

ROLE_URLS = {
    "Admin": ADMIN_URLS | OPERATIONS_URLS | HR_URLS | FINANCE_URLS | DEVICE_REPORT_URLS | {"dashboard"},
    "Guard": GUARD_SELF_SERVICE_URLS,
    "Client": set(),
}

ROLE_PERMISSIONS = {
    "can_people": {"Admin"},
    "can_users": {"Admin"},
    "can_audit_logs": {"Admin"},
    "can_operations": {"Admin"},
    "can_payroll_cases": {"Admin"},
    "can_reports_devices": {"Admin"},
    "can_salaries": {"Admin"},
    "can_cases": {"Admin"},
    "can_devices": {"Admin"},
    "can_guard_self_service": {"Guard"},
    "can_client_self_service": set(),
    "can_dashboard": {"Admin"},
    "can_add_people_profiles": {"Admin"},
}


def user_role_names(user):
    if not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {"Admin"}
    return set(user.groups.values_list("name", flat=True))


def user_is_admin(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name="Admin").exists()
    )


def user_can_access(user, url_name):
    if not url_name or url_name in PUBLIC_URL_NAMES:
        return True
    if not user.is_authenticated:
        return True
    if user_is_admin(user):
        return True
    roles = user_role_names(user)
    if any(url_name in ROLE_URLS.get(role, set()) for role in roles):
        return True

    return False


def nav_permissions(user):
    roles = user_role_names(user)
    if user_is_admin(user):
        roles = {"Admin"}
    return {
        permission: bool(roles & allowed_roles)
        for permission, allowed_roles in ROLE_PERMISSIONS.items()
    }



