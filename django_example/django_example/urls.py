"""URLs for the example project."""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

from blog import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.index, name="index"),
    path("articles/new/", views.create_article, name="create-article"),
    path("articles/<int:pk>/", views.article_detail, name="article-detail"),
    path("articles/<int:pk>/comment/", views.add_comment, name="add-comment"),
]
