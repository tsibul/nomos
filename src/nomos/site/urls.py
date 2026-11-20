from django.urls import path, re_path

from . import views

app_name = 'site'

urlpatterns = [
    path('', views.act_list, name='act_list'),
    path('about/', views.about, name='about'),
    path('robots.txt', views.robots, name='robots'),
    path('<str:jurisdiction>/', views.act_list, name='section'),
    # /<акт>/[@<дата>/][<eid>/][<язык>/ | history/] — разбор в reading.parse_path
    re_path(r'^(?P<path>(?:[^/]+/){3,7})$', views.read, name='read'),
]
