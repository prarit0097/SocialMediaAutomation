from datetime import timedelta

from django.db import migrations


def extend_active_trials(apps, schema_editor):
    """Goodwill: bump existing active 1-day trials to the new 3-day length (+2 days).

    Only touches Trial plan rows that are still active; expired rows are left as-is.
    No-op on a fresh/empty database (e.g. tests).
    """
    UserProfile = apps.get_model("accounts", "UserProfile")
    for profile in UserProfile.objects.filter(
        subscription_plan="Trial", subscription_status="active", subscription_expires_on__isnull=False
    ):
        profile.subscription_expires_on = profile.subscription_expires_on + timedelta(days=2)
        profile.save(update_fields=["subscription_expires_on", "updated_at"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_add_scalability_indexes"),
    ]

    operations = [
        migrations.RunPython(extend_active_trials, noop),
    ]
