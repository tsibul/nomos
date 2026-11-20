from django.db import migrations, models


def fill_keys(apps, schema_editor):
    """Прежние строки: ключ из прежней метки, иначе из вида и id."""
    Amendment = apps.get_model('acts', 'Amendment')
    for amendment in Amendment.objects.all():
        amendment.key = amendment.marker or '%s:%d' % (amendment.kind, amendment.pk)
        amendment.marker = ''
        amendment.save(update_fields=['key', 'marker'])


class Migration(migrations.Migration):

    dependencies = [
        ('acts', '0002_fragment_kind_def'),
    ]

    operations = [
        migrations.AddField(
            model_name='amendment',
            name='key',
            field=models.CharField(default='', max_length=60),
            preserve_default=False,
        ),
        migrations.RunPython(fill_keys, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='amendment',
            constraint=models.UniqueConstraint(
                fields=['amending_act', 'key'],
                name='amendment_act_key_unique',
            ),
        ),
    ]
