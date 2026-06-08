from django.contrib import admin
from .models import *


# ============================================================
# SUPERVISOR ADMIN
# ============================================================
admin.site.site_header = "Guard Management System"
@admin.register(Supervisor)
class SupervisorAdmin(admin.ModelAdmin):
    list_display = (
        "supervisor_id",
        "full_name",
        "phone",
        "email",
        "designation",
    )
    search_fields = (
        "full_name",
        "phone",
        "email",
        "designation",
    )
    list_filter = (
        "designation",
    )


# ============================================================
# GUARD ADMIN
# ============================================================
@admin.register(Guard)
class GuardAdmin(admin.ModelAdmin):
    list_display = (
        "guard_id",
        "employee_number",
        "rfid_card_number",
        "full_name",
        "phone",
        "email",
        "display_supervisor",
        "date_of_joining",
        "status",
    )

    search_fields = (
        "employee_number",
        "rfid_card_number",
        "full_name",
        "phone",
        "email",
        "address",
    )

    list_filter = (
        "status",
        "date_of_joining",
    )

    def display_supervisor(self, obj):
        supervisor = getattr(obj, "supervisor", None)

        if supervisor is None:
            return "No Supervisor"

        if hasattr(supervisor, "all"):
            return ", ".join([str(item) for item in supervisor.all()])

        return supervisor

    display_supervisor.short_description = "Supervisor"


@admin.register(IoTDevice)
class IoTDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id",
        "device_name",
        "device_code",
        "site_location",
        "is_active",
    )

    search_fields = (
        "device_name",
        "device_code",
        "site_location",
    )

    list_filter = (
        "is_active",
        "site_location",
    )


# ============================================================
# CLIENT ADMIN
# ============================================================
@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = (
        "client_id",
        "client_name",
        "contact_person",
        "phone",
        "email",
    )

    search_fields = (
        "client_name",
        "contact_person",
        "phone",
        "email",
        "address",
    )


# ============================================================
# DEPLOYMENT ADMIN
# ============================================================
@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = (
        "deployment_id",
        "guard",
        "client",
        "site_location",
        "start_date",
        "end_date",
        "status",
    )

    search_fields = (
        "guard__full_name",
        "client__client_name",
        "site_location",
    )

    list_filter = (
        "status",
        "start_date",
        "end_date",
    )


# ============================================================
# ASSET ADMIN
# ============================================================
@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "asset_id",
        "asset_name",
        "asset_type",
        "serial_number",
        "guard",
        "purchase_date",
        "status",
    )

    search_fields = (
        "asset_name",
        "asset_type",
        "serial_number",
        "guard__full_name",
    )

    list_filter = (
        "asset_type",
        "status",
        "purchase_date",
    )


# ============================================================
# ATTENDANCE ADMIN
# ============================================================
@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "attendance_id",
        "guard",
        "attendance_date",
        "check_in_time",
        "check_out_time",
        "status",
    )

    search_fields = (
        "guard__full_name",
    )

    list_filter = (
        "status",
        "attendance_date",
    )


# ============================================================
# SHIFT ADMIN
# ============================================================
@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = (
        "shift_id",
        "guard",
        "shift_name",
        "start_time",
        "end_time",
        "shift_type",
    )

    search_fields = (
        "guard__full_name",
        "shift_name",
        "shift_type",
    )

    list_filter = (
        "shift_type",
    )


# ============================================================
# SALARY ADMIN
# ============================================================
@admin.register(Salary)
class SalaryAdmin(admin.ModelAdmin):
    list_display = (
        "salary_id",
        "guard",
        "month",
        "basic_pay",
        "allowances",
        "deductions",
        "net_pay",
        "payment_date",
    )

    search_fields = (
        "guard__full_name",
        "month",
    )

    list_filter = (
        "month",
        "payment_date",
    )

    readonly_fields = (
        "net_pay",
    )


# ============================================================
# INCIDENT ADMIN
# ============================================================
@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = (
        "incident_id",
        "guard",
        "incident_date",
        "incident_type",
        "location",
        "reported_by",
        "status",
    )

    search_fields = (
        "guard__full_name",
        "incident_type",
        "location",
        "description",
    )

    list_filter = (
        "incident_type",
        "status",
        "incident_date",
    )


# ============================================================
# DISCIPLINARY ACTION ADMIN
# ============================================================
@admin.register(DisciplinaryAction)
class DisciplinaryActionAdmin(admin.ModelAdmin):
    list_display = (
        "disciplinary_id",
        "guard",
        "action_date",
        "action_type",
        "penalty",
        "issued_by",
    )

    search_fields = (
        "guard__full_name",
        "action_type",
        "description",
        "penalty",
    )

    list_filter = (
        "action_type",
        "action_date",
    )


# ============================================================
# ADVANCE REQUEST ADMIN
# ============================================================
@admin.register(AdvanceRequest)
class AdvanceRequestAdmin(admin.ModelAdmin):
    list_display = (
        "advance_id",
        "guard",
        "request_date",
        "amount",
        "status",
        "approved_by",
        "approved_date",
    )

    search_fields = (
        "guard__full_name",
        "reason",
    )

    list_filter = (
        "status",
        "request_date",
        "approved_date",
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "user",
        "action",
        "ip_address",
    )
    search_fields = (
        "user__username",
        "action",
        "description",
        "ip_address",
    )
    list_filter = (
        "action",
        "created_at",
    )
    readonly_fields = (
        "user",
        "action",
        "description",
        "ip_address",
        "created_at",
    )
