from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0005_add_scalability_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="connectedaccount",
            name="profile_picture_url",
            field=models.URLField(blank=True, default="", max_length=1000),
        ),
    ]
