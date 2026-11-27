from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("nomos.site.urls")),
]

# админка — только для локальной разработки, на открытом сайте её нет
if settings.DEBUG:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
