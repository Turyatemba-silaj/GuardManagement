from django.db import migrations, models


def normalize_device_numbers(apps, schema_editor):
    IoTDevice = apps.get_model("guardmanagementsystem", "IoTDevice")
    for index, device in enumerate(IoTDevice.objects.order_by("device_id"), start=1):
        device.device_number = f"IOT-{index:03d}"
        if not device.device_code:
            device.device_code = f"IOT-{index:03d}-001"
        device.save(update_fields=["device_number", "device_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("guardmanagementsystem", "0045_guard_rfid_card"),
    ]

    operations = [
        migrations.RenameField(
            model_name="iotdevice",
            old_name="device_name",
            new_name="device_number",
        ),
        migrations.AlterField(
            model_name="iotdevice",
            name="device_number",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RunPython(normalize_device_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="iotdevice",
            name="device_number",
            field=models.CharField(blank=True, max_length=50, unique=True),
        ),
        migrations.RemoveField(
            model_name="iotdevice",
            name="client",
        ),
        migrations.RemoveField(
            model_name="iotdevice",
            name="deployment",
        ),
        migrations.RemoveField(
            model_name="iotdevice",
            name="site_location",
        ),
    ]
