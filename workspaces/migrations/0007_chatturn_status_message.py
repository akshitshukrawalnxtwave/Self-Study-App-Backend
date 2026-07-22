from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0006_unique_topic_slug_per_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatturn",
            name="status_message",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
