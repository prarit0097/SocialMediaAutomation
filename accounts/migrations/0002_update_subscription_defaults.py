from django.db import migrations, models

import accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="subscription_expires_on",
            field=models.DateField(default=accounts.models._default_subscription_expiry),
        ),
        migrations.AlterField(
            model_name="userprofile",
            name="subscription_plan",
            field=models.CharField(default="Trial", max_length=120),
        ),
    ]
