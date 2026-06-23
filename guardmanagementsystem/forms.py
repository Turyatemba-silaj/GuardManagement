from django import forms
from django.db import models
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import Group, User
from calendar import monthrange
from datetime import date
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
    role = forms.ModelChoiceField(queryset=Group.objects.none(), required=True)

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
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.fields["role"].queryset = Group.objects.filter(pk=admin_group.pk)
        self.fields["role"].initial = admin_group
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
        role = self.cleaned_data.get("role")
        user.is_staff = True
        user.is_superuser = True
        if commit:
            user.save()
            if role:
                user.groups.set([role])
        return user


class UserEditForm(forms.ModelForm):
    role = forms.ModelChoiceField(queryset=Group.objects.none(), required=True)

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
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        self.fields["role"].queryset = Group.objects.filter(pk=admin_group.pk)
        self.fields["role"].initial = admin_group
        self.fields["role"].widget.attrs.update({"class": "form-control"})

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data.get("role")
        user.is_staff = True
        user.is_superuser = True
        if commit:
            user.save()
            self.save_m2m()
            user.groups.set([role])
        return user


class EmailOrUsernameAuthenticationForm(AuthenticationForm):
    def clean_username(self):
        username = self.cleaned_data.get("username", "").strip()
        if username and "@" in username:
            email_users = User.objects.filter(email__iexact=username)
            if not User.objects.filter(username__iexact=username).exists():
                user = email_users.first()
                if user:
                    return user.username
        return username


class GuardForm(forms.ModelForm):
    class Meta:
        model = Guard
        exclude = ['user']

        widgets = {
            'rfid_card': forms.Select(attrs={
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
        self.fields['device_number'].required = False
        self.fields['device_code'].required = False
        self.fields['api_key'].required = False

    class Meta:
        model = IoTDevice
        fields = ['device_number', 'device_code', 'api_key', 'is_active']

        widgets = {
            'device_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Example: IOT-001; auto-generated if blank'
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

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        exclude = ['user']

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


class ContractForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.order_by('client_name')

        selected_device_id = self.data.get('iot_device') if self.is_bound else self.instance.iot_device_id
        unavailable_device_ids = Contract.objects.filter(
            iot_device__isnull=False,
            status__in=['Draft', 'Active'],
        )
        if self.instance and self.instance.pk:
            unavailable_device_ids = unavailable_device_ids.exclude(pk=self.instance.pk)
        unavailable_device_ids = unavailable_device_ids.values_list('iot_device_id', flat=True)

        device_queryset = IoTDevice.objects.filter(is_active=True).exclude(
            pk__in=unavailable_device_ids
        )

        if selected_device_id:
            device_queryset = IoTDevice.objects.filter(
                models.Q(pk=selected_device_id) | models.Q(pk__in=device_queryset.values('pk'))
            )

        self.fields['iot_device'].queryset = device_queryset.order_by(
            'device_number',
        )
        self.fields['iot_device'].label_from_instance = (
            lambda device: f"{device.device_number} ({device.device_code})"
        )
        self.fields['iot_device'].help_text = "Pick an active, unassigned IoT device from inventory."
        self.fields['contract_number'].required = False
        self.fields['contract_number'].help_text = "Leave blank to generate the next contract number."

    class Meta:
        model = Contract
        fields = [
            'client',
            'contract_number',
            'number_of_guards',
            'day_shift_guards',
            'night_shift_guards',
            'charge_per_guard',
            'contract_type',
            'location',
            'iot_device',
            'start_date',
            'end_date',
            'status',
            'terms',
        ]

        widgets = {
            'client': forms.Select(attrs={
                'class': 'form-control'
            }),
            'contract_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Auto-generated if blank'
            }),
            'number_of_guards': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '1'
            }),
            'day_shift_guards': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
            'night_shift_guards': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
            'charge_per_guard': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter monthly charge per guard',
                'step': '0.01'
            }),
            'contract_type': forms.Select(attrs={
                'class': 'form-control'
            }),
            'location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Client site or deployment location'
            }),
            'iot_device': forms.Select(attrs={
                'class': 'form-control'
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
            'terms': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter contract terms, billing notes, or service conditions',
                'rows': 4
            }),
        }


class DeploymentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        used_contract_ids = Deployment.objects.exclude(contract__isnull=True)
        if self.instance and self.instance.pk:
            used_contract_ids = used_contract_ids.exclude(pk=self.instance.pk)
        used_contract_ids = used_contract_ids.values_list('contract_id', flat=True)

        self.fields['contract'].queryset = Contract.objects.select_related('client').exclude(
            status__in=['Expired', 'Terminated']
        ).exclude(
            pk__in=used_contract_ids
        ).order_by(
            'client__client_name',
            'contract_number',
        )
        self.fields['contract'].required = True
        self.fields['contract'].label_from_instance = (
            lambda contract: f"{contract.contract_number} - {contract.client.client_name} - {contract.location or 'No location'} ({contract.number_of_guards} guards: D {contract.day_shift_guards}, N {contract.night_shift_guards})"
        )
        self.fields['start_date'].required = False
        self.fields['end_date'].required = False
        self.fields['start_date'].help_text = "Auto-filled from the contract start date when left blank."
        self.fields['end_date'].help_text = "Auto-filled from the contract end date when left blank."

    def clean(self):
        cleaned_data = super().clean()
        contract = cleaned_data.get('contract')

        if not contract:
            self.add_error('contract', "Select a contract for this deployment.")
            return cleaned_data

        duplicate = Deployment.objects.filter(contract=contract)
        if self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            self.add_error('contract', "This contract is already linked to another deployment.")

        cleaned_data['client'] = contract.client
        cleaned_data['site_location'] = contract.location
        if not cleaned_data.get('start_date'):
            cleaned_data['start_date'] = contract.start_date
        if not cleaned_data.get('end_date'):
            cleaned_data['end_date'] = contract.end_date

        return cleaned_data

    class Meta:
        model = Deployment
        fields = [
            'contract',
            'shift',
            'start_date',
            'end_date',
            'status',
        ]

        widgets = {
            'contract': forms.Select(attrs={
                'class': 'form-control'
            }),
            'shift': forms.Select(attrs={
                'class': 'form-control'
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
        }


class DeploymentGuardForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['deployment'].queryset = Deployment.objects.select_related('client', 'contract').order_by(
            'client__client_name',
            'site_location',
        )
        self.fields['deployment'].label_from_instance = (
            lambda deployment: f"{deployment.client.client_name} - {deployment.site_location} ({deployment.status})"
        )
        self.fields['guard'].queryset = Guard.objects.filter(status="Active").order_by('full_name')
        self.fields['deployment_date'].label = "Deployment Date"

    def clean(self):
        cleaned_data = super().clean()
        deployment = cleaned_data.get('deployment')
        guard = cleaned_data.get('guard')
        deployment_date = cleaned_data.get('deployment_date')

        if deployment and deployment_date:
            if deployment_date < deployment.start_date:
                raise forms.ValidationError("Guard deployment date cannot be before the deployment start date.")

            if deployment.end_date and deployment_date > deployment.end_date:
                raise forms.ValidationError("Guard deployment date cannot be after the deployment end date.")

        if deployment and guard and deployment_date:
            duplicate = DeploymentGuard.objects.filter(
                deployment=deployment,
                guard=guard,
                deployment_date=deployment_date,
            )
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)

            if duplicate.exists():
                raise forms.ValidationError("This guard is already assigned to this deployment date.")

        return cleaned_data

    class Meta:
        model = DeploymentGuard
        fields = [
            'deployment',
            'guard',
            'deployment_date',
            'shift_type',
        ]

        widgets = {
            'deployment': forms.Select(attrs={
                'class': 'form-control'
            }),
            'deployment_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'shift_type': forms.Select(attrs={
                'class': 'form-control'
            }),
        }

class DeploymentGuardBulkForm(forms.Form):
    deployment = forms.ModelChoiceField(
        queryset=Deployment.objects.none(),
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    guards = forms.ModelMultipleChoiceField(
        queryset=Guard.objects.none(),
        label="Guards",
        help_text="Tick all guards to assign for swipe attendance for the whole selected month.",
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'guard-checkbox-list'})
    )
    deployment_date = forms.DateField(
        label="Deployment Month",
        initial=date.today,
        help_text="Pick any date in the month to generate daily guard deployments for that full month.",
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    shift_type = forms.ChoiceField(
        choices=DeploymentGuard.SHIFT_TYPE_CHOICES,
        initial='D',
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['deployment'].queryset = Deployment.objects.select_related('client', 'contract').order_by(
            'client__client_name',
            'site_location',
        )
        self.fields['deployment'].label_from_instance = (
            lambda deployment: f"{deployment.client.client_name} - {deployment.site_location} ({deployment.status})"
        )
        self.fields['guards'].queryset = Guard.objects.filter(status="Active").order_by('full_name')

    def clean(self):
        cleaned_data = super().clean()
        deployment = cleaned_data.get('deployment')
        guards = list(cleaned_data.get('guards') or [])
        deployment_date = cleaned_data.get('deployment_date')

        if deployment and deployment_date:
            if deployment_date < deployment.start_date:
                raise forms.ValidationError("Guard deployment date cannot be before the deployment start date.")

            if deployment.end_date and deployment_date > deployment.end_date:
                raise forms.ValidationError("Guard deployment date cannot be after the deployment end date.")

        if deployment and guards and deployment_date:
            if deployment.contract_id:
                active_statuses = ["Active", "Running", "On Deployment", "Available", "Has No Deployment"]
                new_guard_ids = {guard.guard_id for guard in guards}
                last_day = monthrange(deployment_date.year, deployment_date.month)[1]
                month_start = date(deployment_date.year, deployment_date.month, 1)
                month_end = date(deployment_date.year, deployment_date.month, last_day)
                schedule_start = max(month_start, deployment.start_date)
                schedule_end = min(month_end, deployment.end_date) if deployment.end_date else month_end

                if schedule_start <= schedule_end:
                    for day_number in range(schedule_start.day, schedule_end.day + 1):
                        schedule_date = date(schedule_start.year, schedule_start.month, day_number)
                        existing_guard_ids = set(DeploymentGuard.objects.filter(
                            deployment__contract=deployment.contract,
                            deployment__status__in=active_statuses,
                            deployment_date=schedule_date,
                        ).values_list('guard_id', flat=True))
                        if len(existing_guard_ids | new_guard_ids) > deployment.contract.number_of_guards:
                            raise forms.ValidationError(
                                f"you have excedd contacted number of guards on {schedule_date:%Y-%m-%d}"
                            )

        return cleaned_data

class ProgramGuardForm(forms.ModelForm):
    guard = forms.ModelChoiceField(
        queryset=Guard.objects.filter(status="Active"),
        required=True,
        label="Main Guard",
        widget=forms.Select(attrs={
            'class': 'form-control'
        })
    )
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
        fields = ['client', 'site_location', 'start_date', 'end_date']

        widgets = {
            'client': forms.Select(attrs={
                'class': 'form-control'
            }),
            'site_location': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter site location'
            }),
            'shift': forms.Select(attrs={
                'class': 'form-control'
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

            active_deployment = DeploymentGuard.objects.filter(
                guard=guard,
                deployment_date=start_time,
                deployment__status__in=["Active", "Running", "On Deployment", "Available", "Has No Deployment"],
                deployment__start_date__lte=start_time,
            ).filter(
                models.Q(deployment__end_date__isnull=True) | models.Q(deployment__end_date__gte=start_time)
            ).exists()

            if not active_deployment:
                raise forms.ValidationError("This guard is not actively scheduled on any client site for this shift date.")

        return cleaned_data

    class Meta:
        model = Shift
        fields = '__all__'

        widgets = {
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
            'status': 'Status',
        }

        widgets = {
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
            'status': forms.Select(attrs={
                'class': 'form-control'
            }),
        }

