from django.contrib import admin

from .models import Note, Term, TMSegment

admin.site.register(Note)
admin.site.register(Term)
admin.site.register(TMSegment)
