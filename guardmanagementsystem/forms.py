from django import forms
from django.db import models
from .models import*


class GuardForm(forms.ModelForm):
    class Meta:
        model = Guard
        fields = '__all__'

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'full_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter guard full name'
            }),
            'date_of_birth': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter phone number'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter email address'
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter address',
                'rows': 3
            }),
            'date_of_joining': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
            'daily_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter daily rate',
                'step': '0.01'
            }),
        }

class SupervisorForm(forms.ModelForm):
    class Meta:
        model = Supervisor
        fields = '__all__'

        widgets = {
            'full_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter supervisor full name'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter phone number'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter email address'
            }),
            'designation': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter designation'
            }),
            'daily_rate': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Supervisor daily rate',
                'step': '0.01'
            }),
        }

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields ='__all__'

        widgets = {
            'client_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter client name'
            }),
            'contact_person': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter contact person'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter phone number'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter email address'
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter client address',
                'rows': 3
            }),
        }

class DeploymentForm(forms.ModelForm):
    class Meta:
        model = Deployment
        fields = '__all__'
            
        
        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'client': forms.Select(attrs={
                'class': 'form-control'
            }),
            'site_location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter site location'
            }),
            'start_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'end_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
            'replacement_guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'absence_reason': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Reason when guard is not present',
                'rows': 3
            }),
        }


class ProgramGuardForm(forms.ModelForm):
    class Meta:
        model = Deployment
        fields = ['guard', 'client', 'site_location', 'start_date', 'end_date']

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'client': forms.Select(attrs={
                'class': 'form-control'
            }),
            'site_location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter site location'
            }),
            'start_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'end_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
        }


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = '__all__'

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'asset_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter asset name'
            }),
            'asset_type': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Example: Uniform, Radio, Baton'
            }),
            'serial_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter serial number'
            }),
            'purchase_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
        }


class AttendanceForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        guard = cleaned_data.get('guard')
        attendance_date = cleaned_data.get('attendance_date')
        replacement_guard = cleaned_data.get('replacement_guard')
        status = cleaned_data.get('status')
        absence_reason = cleaned_data.get('absence_reason')

        if guard and attendance_date:
            duplicate = Attendance.objects.filter(guard=guard, attendance_date=attendance_date)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)

            if duplicate.exists():
                raise forms.ValidationError("This guard already has attendance recorded for this date.")

            active_deployment = Deployment.objects.filter(
                guard=guard,
                status="Active",
                start_date__lte=attendance_date,
            ).filter(
                models.Q(end_date__isnull=True) | models.Q(end_date__gte=attendance_date)
            ).exists()

            if not active_deployment:
                raise forms.ValidationError("This guard is not actively programmed on any client site for this date.")

        if replacement_guard and guard and replacement_guard == guard:
            raise forms.ValidationError("Replacement guard cannot be the same as the scheduled guard.")

        if replacement_guard and attendance_date:
            replacement_active = Deployment.objects.filter(
                guard=replacement_guard,
                status="Active",
                start_date__lte=attendance_date,
            ).filter(
                models.Q(end_date__isnull=True) | models.Q(end_date__gte=attendance_date)
            ).exists()

            if not replacement_active:
                raise forms.ValidationError("Replacement guard is not actively programmed on any client site for this date.")

        if status == "Absent" and not absence_reason and not replacement_guard:
            raise forms.ValidationError("Enter an absence reason or select a replacement guard.")

        return cleaned_data

    class Meta:
        model = Attendance
        fields = '__all__'
        
        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'attendance_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'check_in_time': forms.TimeInput(attrs={
                'class': 'form-control',
                'type': 'time'
            }),
            'check_out_time': forms.TimeInput(attrs={
                'class': 'form-control',
                'type': 'time'
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
        }

class ShiftForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        guard = cleaned_data.get('guard')
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')

        if start_time and end_time and end_time < start_time:
            raise forms.ValidationError("Shift end date cannot be before the start date.")

        if guard and start_time:
            duplicate = Shift.objects.filter(guard=guard, start_time=start_time)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)

            if duplicate.exists():
                raise forms.ValidationError("This guard already has a shift on this date.")

            active_deployment = Deployment.objects.filter(
                guard=guard,
                status="Active",
                start_date__lte=start_time,
            ).filter(
                models.Q(end_date__isnull=True) | models.Q(end_date__gte=start_time)
            ).exists()

            if not active_deployment:
                raise forms.ValidationError("This guard is not actively programmed on any client site for this shift date.")

        return cleaned_data

    class Meta:
        model = Shift
        fields = '__all__'

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'shift_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Example: Morning Shift'
            }),
            'start_time': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'end_time': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'shift_type': forms.Select(attrs={
                'class': 'form-control'
            }),
        }


class SalaryForm(forms.ModelForm):
    class Meta:
        model = Salary
        fields = [
            'employee_type',
            'guard',
            'supervisor',
            'month',
            'year',
            'allowances',
            'deductions',
            'payment_date',
        ]

        widgets = {
            'employee_type': forms.Select(attrs={
                'class': 'form-control'
            }),
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'supervisor': forms.Select(attrs={
                'class': 'form-control'
            }),
            'month': forms.Select(attrs={
                'class': 'form-control'
            }),
            'year': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter payroll year'
            }),
            'allowances': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter allowances',
                'step': '0.01'
            }),
            'deductions': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter deductions',
                'step': '0.01'
            }),
            'payment_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
        }


class IncidentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reported_by'].required = True

    class Meta:
        model = Incident
        fields = [
            'guard',
            'incident_date',
            'incident_time',
            'reporter_title',
            'reporter_first_name',
            'reporter_middle_name',
            'reporter_last_name',
            'location',
            'incident_type',
            'description',
            'involved_first_name',
            'involved_last_name',
            'further_comments',
            'reported_by',
            'status',
        ]

        labels = {
            'guard': 'Guard',
            'incident_date': 'Date when incident occurred',
            'incident_time': 'Time when incident occurred',
            'reporter_title': 'Incident report issued by',
            'reporter_first_name': 'First Name',
            'reporter_middle_name': 'Middle Name',
            'reporter_last_name': 'Last Name',
            'location': 'Incident Location (Please provide specific details)',
            'incident_type': 'Nature of incident',
            'description': 'Incident details',
            'involved_first_name': 'Full Name - First Name',
            'involved_last_name': 'Full Name - Last Name',
            'further_comments': 'Further Comments',
            'reported_by': 'Supervisor',
            'status': 'Status',
        }

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'incident_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
                'placeholder': 'MM-DD-YYYY'
            }),
            'incident_time': forms.TimeInput(attrs={
                'class': 'form-control',
                'type': 'time'
            }),
            'incident_type': forms.Select(attrs={
                'class': 'form-control'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter incident details',
                'rows': 4
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Provide specific incident location details'
            }),
            'reporter_title': forms.Select(attrs={
                'class': 'form-control'
            }),
            'reporter_first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'First Name'
            }),
            'reporter_middle_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Middle Name'
            }),
            'reporter_last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Last Name'
            }),
            'involved_first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'First Name'
            }),
            'involved_last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Last Name'
            }),
            'further_comments': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter further comments',
                'rows': 3
            }),
            'reported_by': forms.Select(attrs={
                'class': 'form-control'
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
        }


class DisciplinaryActionForm(forms.ModelForm):
    class Meta:
        model = DisciplinaryAction
        fields = '__all__'

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'action_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'action_type': forms.Select(attrs={
                'class': 'form-control'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Describe disciplinary action',
                'rows': 4
            }),
            'penalty': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter penalty if any'
            }),
            'issued_by': forms.Select(attrs={
                'class': 'form-control'
            }),
        }
