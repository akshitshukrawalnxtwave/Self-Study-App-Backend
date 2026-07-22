from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0007_chatturn_status_message"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatturn",
            name="current_lesson_path",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
    ]
