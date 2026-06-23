from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("guardmanagementsystem", "0046_iotdevice_inventory_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="deploymentguard",
            name="shift_type",
            field=models.CharField(
                choices=[
                    ("D", "D"),
                    ("N", "N"),
                    ("D/N", "D/N"),
                    ("PH", "PH"),
                ],
                default="D",
                max_length=30,
            ),
        ),
        migrations.RemoveField(
            model_name="deploymentguard",
            name="check_in_time",
        ),
        migrations.RemoveField(
            model_name="deploymentguard",
            name="check_out_time",
        ),
    ]
