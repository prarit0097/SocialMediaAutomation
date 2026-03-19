from django.db import migrations


def normalize_legacy_starter_plan(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserProfile.objects.filter(subscription_plan="Starter").update(subscription_plan="Trial")


def revert_legacy_starter_plan(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserProfile.objects.filter(subscription_plan="Trial").update(subscription_plan="Starter")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_update_subscription_defaults"),
    ]

    operations = [
        migrations.RunPython(normalize_legacy_starter_plan, revert_legacy_starter_plan),
    ]
