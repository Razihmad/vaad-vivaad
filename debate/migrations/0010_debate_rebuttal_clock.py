from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('debate', '0009_debate_user_con_disconnected_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='debate',
            name='debate_time_seconds',
            field=models.IntegerField(default=180),
        ),
        migrations.AddField(
            model_name='debate',
            name='pro_time_remaining_seconds',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='debate',
            name='con_time_remaining_seconds',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='debate',
            name='timed_out_side',
            field=models.CharField(
                blank=True,
                choices=[('PRO', 'Pro'), ('CON', 'Con')],
                max_length=20,
                null=True,
            ),
        ),
    ]
