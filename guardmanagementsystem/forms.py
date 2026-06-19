from django import forms
from django.db import models
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import Group, User
from .models import*


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save()
        return user


class UserCreateForm(UserCreationForm):
    email = forms.EmailField(required=False)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    is_active = forms.BooleanField(required=False, initial=True)
    is_staff = forms.BooleanField(required=False)
    is_superuser = forms.BooleanField(required=False)
    role = forms.ModelChoiceField(queryset=Group.objects.none(), required=False)

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "role",
            "password1",
            "password2",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Group.objects.filter(
            name__in=[
                "Admin",
                "Supervisor",
                "Guard",
                "Client",
            ]
        ).order_by("name")
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control"})

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get("email", "")
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.is_active = self.cleaned_data.get("is_active", False)
        user.is_staff = self.cleaned_data.get("is_staff", False)
        user.is_superuser = self.cleaned_data.get("is_superuser", False)
        if commit:
            user.save()
            role = self.cleaned_data.get("role")
            if role:
                user.groups.set([role])
        return user


class UserEditForm(forms.ModelForm):
    role = forms.ModelChoiceField(queryset=Group.objects.none(), required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active", "is_staff", "is_superuser", "role"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_staff": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_superuser": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Group.objects.filter(
            name__in=[
                "Admin",
                "Supervisor",
                "Guard",
                "Client",
            ]
        ).order_by("name")
        self.fields["role"].widget.attrs.update({"class": "form-control"})
        role = self.instance.groups.filter(name__in=self.fields["role"].queryset.values_list("name", flat=True)).first()
        if role:
            self.fields["role"].initial = role

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            role = self.cleaned_data.get("role")
            if role:
                user.groups.set([role])
            else:
                user.groups.clear()
        return user


class EmailOrUsernameAuthenticationForm(AuthenticationForm):
    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if username and "@" in username:
            email_users = User.objects.filter(email__iexact=username)
            supervisor_user = email_users.filter(username__icontains="supervisor").first()
            if supervisor_user:
                return supervisor_user.username

            if not User.objects.filter(username__iexact=username).exists():
                user = email_users.first()
                if user:
                    return user.username
        return username


class GuardForm(forms.ModelForm):
    class Meta:
        model = Guard
        exclude = ['user', 'rfid_card_number']

        widgets = {
            'rfid_card': forms.Select(attrs={
                'class': 'form-control'
            }),
            'supervisor': forms.Select(attrs={
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


class RFIDCardForm(forms.ModelForm):
    class Meta:
        model = RFIDCard
        fields = '__all__'

        widgets = {
            'card_uid': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter RFID card UID'
            }),
            'card_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter printed card number'
            }),
            'issue_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
        }


class IoTDeviceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.order_by('client_name')
        self.fields['deployment'].queryset = Deployment.objects.select_related('client', 'guard').order_by('client__client_name', 'site_location', 'guard__full_name')
        self.fields['deployment'].label_from_instance = (
            lambda deployment: f"{deployment.client.client_name} - {deployment.site_location} - {deployment.guard.full_name}"
        )
        self.fields['device_name'].required = False
        self.fields['device_code'].required = False
        self.fields['api_key'].required = False

    def clean(self):
        cleaned_data = super().clean()
        client = cleaned_data.get('client')
        deployment = cleaned_data.get('deployment')

        if not deployment:
            raise forms.ValidationError("Select a deployment so the device can pick its client and site.")

        if client and deployment.client_id != client.client_id:
            raise forms.ValidationError("Selected deployment does not belong to the selected client.")

        cleaned_data['client'] = deployment.client
        return cleaned_data

    class Meta:
        model = IoTDevice
        fields = ['client', 'deployment', 'device_name', 'device_code', 'api_key', 'is_active']

        widgets = {
            'client': forms.Select(attrs={
                'class': 'form-control'
            }),
            'deployment': forms.Select(attrs={
                'class': 'form-control'
            }),
            'device_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-generated from client and location if blank'
            }),
            'device_code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-generated if blank'
            }),
            'api_key': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-generated secure API key if blank'
            }),
            'is_active': forms.Select(attrs={
                'class': 'form-control'
            }),
        }

class SupervisorForm(forms.ModelForm):
    class Meta:
        model = Supervisor
        exclude = ['user']

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
            'user': forms.Select(attrs={
                'class': 'form-control'
            }),
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


class ClientCommunicationForm(forms.ModelForm):
    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        if client:
            self.instance.client = client

    class Meta:
        model = ClientCommunication
        fields = ['message_type', 'subject', 'location', 'description']
        widgets = {
            'message_type': forms.Select(attrs={
                'class': 'form-control'
            }),
            'subject': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter subject'
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Site or location, if applicable'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 5,
                'placeholder': 'Describe the service request, complaint, feedback, incident, or message'
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
    additional_guards = forms.ModelMultipleChoiceField(
        queryset=Guard.objects.filter(status="Active"),
        required=False,
        label="Additional Guards",
        help_text="Select the other regular guards needed to meet the required site headcount.",
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control',
            'size': '6'
        })
    )
    reliever_guard = forms.ModelChoiceField(
        queryset=Guard.objects.filter(status="Active"),
        required=True,
        label="Reliever Guard",
        help_text="This guard is scheduled on every seventh day while the main guard is off.",
        widget=forms.Select(attrs={
            'class': 'form-control'
        })
    )
    required_guards = forms.IntegerField(
        min_value=1,
        initial=1,
        label="Required Guards",
        help_text="Maximum number of guards allowed on this client site per scheduled day.",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '1'
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_guards = Guard.objects.filter(status="Active")
        self.fields["guard"].queryset = active_guards
        self.fields["reliever_guard"].queryset = active_guards
        self.fields["additional_guards"].queryset = active_guards

    def clean(self):
        cleaned_data = super().clean()
        guard = cleaned_data.get("guard")
        additional_guards = list(cleaned_data.get("additional_guards") or [])
        reliever_guard = cleaned_data.get("reliever_guard")
        required_guards = cleaned_data.get("required_guards")

        if guard and reliever_guard and guard == reliever_guard:
            raise forms.ValidationError("Reliever guard must be different from the main scheduled guard.")

        if guard and guard in additional_guards:
            raise forms.ValidationError("Main guard should not be repeated in additional guards.")

        if reliever_guard and reliever_guard in additional_guards:
            raise forms.ValidationError("Reliever guard should not be selected as an additional regular guard.")

        if required_guards and guard:
            regular_guard_count = 1 + len(additional_guards)
            if regular_guard_count < required_guards:
                raise forms.ValidationError(
                    f"Select {required_guards - 1} additional regular guard(s) so the team meets the required guard count."
                )

        return cleaned_data

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
                raise forms.ValidationError("This guard is not actively scheduled on any client site for this date.")

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
                raise forms.ValidationError("Replacement guard is not actively scheduled on any client site for this date.")

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
        shift_type = cleaned_data.get('shift_type')

        if str(shift_type or "").strip().upper() in ["N/D", "ND"]:
            raise forms.ValidationError("Use D/N for combined day and night duty. N/D pattern is not allowed.")

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
                raise forms.ValidationError("This guard is not actively scheduled on any client site for this shift date.")

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


class AdvanceRequestForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["installment_amount"].required = False
        self.fields["recovery_period_months"].initial = 6

    def clean_recovery_period_months(self):
        return 6

    class Meta:
        model = AdvanceRequest
        fields = [
            'guard',
            'review_supervisor',
            'amount',
            'installment_amount',
            'recovery_period_months',
            'reason',
            'status',
            'approved_by',
            'approved_date',
            'approval_reason',
            'rejection_reason',
        ]

        widgets = {
            'guard': forms.Select(attrs={
                'class': 'form-control'
            }),
            'review_supervisor': forms.Select(attrs={
                'class': 'form-control'
            }),
            'amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'placeholder': 'Enter advance amount'
            }),
            'installment_amount': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'readonly': 'readonly',
                'placeholder': 'Auto calculated as 20% of gross pay'
            }),
            'recovery_period_months': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '6',
                'max': '6',
                'readonly': 'readonly',
            }),
            'reason': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter reason for salary advance',
                'rows': 3
            }),
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
            'approved_by': forms.Select(attrs={
                'class': 'form-control'
            }),
            'approved_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'approval_reason': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Required when approving an advance request'
            }),
            'rejection_reason': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Required when rejecting an advance request'
            }),
        }


class RemoteAdvanceRequestForm(forms.Form):
    guard_number = forms.CharField(
        max_length=20,
        label="Guard Number",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Example: GUARD001"
        })
    )
    phone = forms.CharField(
        max_length=20,
        label="Phone Number",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter the phone number on your guard profile"
        })
    )
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "placeholder": "Enter requested advance amount"
        })
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 4,
            "placeholder": "Explain why you need this salary advance"
        })
    )

    def clean(self):
        cleaned_data = super().clean()
        guard_number = str(cleaned_data.get("guard_number") or "").strip()
        phone = str(cleaned_data.get("phone") or "").strip()

        guard = Guard.objects.filter(guard_number__iexact=guard_number, status="Active").first()
        if not guard:
            raise forms.ValidationError("No active guard was found with this guard number.")

        if phone and str(guard.phone or "").strip() != phone:
            raise forms.ValidationError("The phone number does not match this guard profile.")

        if not guard.supervisor:
            raise forms.ValidationError("This guard does not have an assigned supervisor for advance review.")

        if not guard.supervisor.email:
            raise forms.ValidationError("This guard's assigned supervisor does not have an email address for notifications.")

        cleaned_data["guard"] = guard
        return cleaned_data


class GuardSelfAdvanceRequestForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.01",
            "placeholder": "Enter requested advance amount"
        })
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 4,
            "placeholder": "Explain why you need this salary advance"
        })
    )
