from django.contrib.auth import views as auth_views
from django.urls import path, re_path

from . import analytics, views

app_name = "site"

urlpatterns = [
    path("", views.act_list, name="act_list"),
    path("about/", views.about, name="about"),
    path("robots.txt", views.robots, name="robots"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="site/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("analytics/", analytics.index, name="analytics"),
    path(
        "analytics/changes/<str:on>/",
        analytics.changes,
        name="changes",
    ),
    path(
        "analytics/changes/<str:on>/<str:uid>/",
        analytics.compare,
        name="compare",
    ),
    path(
        "analytics/changes/<str:on>/<str:uid>/<str:lang>/",
        analytics.compare,
        name="compare_lang",
    ),
    path("<str:jurisdiction>/", views.act_list, name="section"),
    path(
        "<str:jurisdiction>/<str:subsection>/",
        views.act_list,
        name="subsection",
    ),
    # /<акт>/[@<дата>/][<eid>/][history/][<язык>/] — разбор в
    # reading.parse_path
    re_path(r"^(?P<path>(?:[^/]+/){3,8})$", views.read, name="read"),
]
