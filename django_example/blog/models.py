"""Models stored in minidb."""

from __future__ import annotations

from django.db import models

from minidb.django import MinidbModel


class Article(MinidbModel):
    """A blog article, stored as a document."""

    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    views = models.IntegerField(default=0)
    published = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)

    class Meta:
        app_label = "blog"
        db_table = "articles"

    def __str__(self) -> str:
        return self.title


class Comment(MinidbModel):
    """A comment on an article, stored as a document."""

    article_id = models.IntegerField()
    author = models.CharField(max_length=100)
    text = models.TextField()

    class Meta:
        app_label = "blog"
        db_table = "comments"

    def __str__(self) -> str:
        return f"{self.author}: {self.text[:30]}"
