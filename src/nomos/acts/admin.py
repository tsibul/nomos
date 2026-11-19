from django.contrib import admin

from . import models

for model in (
    models.Act,
    models.ActRelation,
    models.Amendment,
    models.Expression,
    models.Fragment,
    models.FragmentAlias,
    models.FragmentText,
    models.FragmentVersion,
    models.Reference,
):
    admin.site.register(model)
