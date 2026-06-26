from django.db import migrations


def delete_orphan_null_user_accounts(apps, schema_editor):
    """Remove legacy ConnectedAccount rows with no owning user.

    The (user, platform, page_id) unique constraint treats NULL users as distinct
    in PostgreSQL, so pre-ownership rows (user IS NULL) duplicated the real, owned
    rows — surfacing as duplicate accounts in catalog/diagnostic views. Every live
    code path is user-scoped, so an unowned row is unreachable dead weight; delete it.
    """
    ConnectedAccount = apps.get_model("integrations", "ConnectedAccount")
    ConnectedAccount.objects.filter(user__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0006_connectedaccount_profile_picture_url"),
    ]

    operations = [
        migrations.RunPython(delete_orphan_null_user_accounts, migrations.RunPython.noop),
    ]
