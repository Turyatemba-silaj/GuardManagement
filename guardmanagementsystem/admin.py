from django.contrib import admin
from .models import *


@admin.register(RFIDCard)
class RFIDCardAdmin(admin.ModelAdmin):
    list_display = (
        "rfid_card_id",
        "card_uid",
        "card_number",
        "issue_date",
        "status",
    )

    search_fields = (
        "card_uid",
        "card_number",
    )

    list_filter = (
        "status",
        "issue_date",
    )

@admin.register(Guard)
class GuardAdmin(admin.ModelAdmin):
    exclude = (
        "user",
    )

    list_display = (
        "guard_id",
        "user",
        "full_name",
        "phone",
        "email",
        "date_of_joining",
        "daily_rate",
        "status",
    )

    search_fields = (
        "user__username",
        "full_name",
        "phone",
        "email",
        "address",
    )

    list_filter = (
        "status",
        "date_of_joining",
    )

@admin.register(IoTDevice)
class IoTDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "device_id",
        "device_name",
        "device_code",
        "client",
        "deployment",
        "site_location",
        "is_active",
    )

    search_fields = (
        "device_name",
        "device_code",
        "client__client_name",
        "deployment__deployment_guards__guard__full_name",
        "site_location",
    )

    list_filter = (
        "is_active",
        "site_location",
    )

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    exclude = (
        "user",
    )

    list_display = (
        "client_id",
        "user",
        "client_name",
        "contact_person",
        "phone",
        "email",
        "address",
    )

    search_fields = (
        "user__username",
        "client_name",
        "contact_person",
        "phone",
        "email",
        "address",
    )


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "contract_id",
        "contract_number",
        "client",
        "location",
        "number_of_guards",
        "day_shift_guards",
        "night_shift_guards",
        "charge_per_guard",
        "monthly_value",
        "contract_type",
        "assigned_rfid_cards",
        "status",
        "start_date",
        "end_date",
    )

    search_fields = (
        "contract_number",
        "client__client_name",
        "location",
        "contract_type",
        "rfid_cards__card_uid",
        "rfid_cards__card_number",
    )

    list_filter = (
        "status",
        "contract_type",
        "start_date",
        "end_date",
        "status",
    )

    readonly_fields = (
        "monthly_value",
    )

    def assigned_rfid_cards(self, obj):
        cards = obj.rfid_cards.values_list("card_number", flat=True).distinct()
        return ", ".join(cards) or "-"

@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = (
        "deployment_id",
        "assigned_guards",
        "client",
        "contract",
        "site_location",
        "start_date",
        "end_date",
        "status",
    )

    search_fields = (
        "deployment_guards__guard__full_name",
        "deployment_guards__guard__guard_number",
        "client__client_name",
        "contract__contract_number",
        "site_location",
    )

    list_filter = (
        "status",
        "start_date",
        "end_date",
        "status",
    )

    def assigned_guards(self, obj):
        guards = [assignment.guard.full_name for assignment in obj.deployment_guards.all()]
        return ", ".join(guards) or "-"


@admin.register(DeploymentGuard)
class DeploymentGuardAdmin(admin.ModelAdmin):
    list_display = (
        "deployment_guard_id",
        "deployment",
        "guard",
        "deployment_date",
        "check_in_time",
        "check_out_time",
        "status",
    )

    search_fields = (
        "deployment__client__client_name",
        "deployment__site_location",
        "guard__full_name",
        "guard__guard_number",
    )

    list_filter = (
        "deployment_date",
        "status",
    )

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


@admin.register(AssetAssignmentHistory)
class AssetAssignmentHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "history_id",
        "asset",
        "guard",
        "assigned_date",
        "returned_date",
        "condition_on_return",
    )

    search_fields = (
        "asset__asset_name",
        "asset__serial_number",
        "guard__full_name",
        "notes",
    )

    list_filter = (
        "assigned_date",
        "returned_date",
        "condition_on_return",
    )

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

@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = (
        "incident_id",
        "guard",
        "incident_date",
        "incident_type",
        "location",
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
