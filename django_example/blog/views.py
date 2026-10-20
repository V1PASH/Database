"""Views for the blog, reading and writing through minidb."""

from __future__ import annotations

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from .models import Article, Comment


def index(request):
    """The articles, newest first."""
    articles = Article.objects.filter(published=True).order_by("-id")
    return render(
        request,
        "blog/index.html",
        {"articles": articles, "total": Article.objects.count()},
    )


def article_detail(request, pk: int):
    """One article and its comments."""
    article = Article.objects.get(pk=pk)
    comments = Comment.objects.filter(article_id=pk)
    return render(
        request, "blog/detail.html", {"article": article, "comments": comments}
    )


def create_article(request):
    """Make an article and go to it."""
    if request.method == "POST":
        article = Article.objects.create(
            title=request.POST.get("title", "Untitled"),
            body=request.POST.get("body", ""),
            published=bool(request.POST.get("published")),
            views=0,
        )
        return HttpResponseRedirect(reverse("article-detail", args=[article.pk]))

    return render(request, "blog/create.html")


def add_comment(request, pk: int):
    """Add a comment to an article."""
    if request.method == "POST":
        Comment.objects.create(
            article_id=pk,
            author=request.POST.get("author", "anonymous"),
            text=request.POST.get("text", ""),
        )
        return HttpResponseRedirect(reverse("article-detail", args=[pk]))
    return render(request, "blog/comment.html", {"article_id": pk})
