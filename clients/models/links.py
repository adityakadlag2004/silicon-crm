"""Business Links: categories, links, favorites."""

from django.conf import settings
from django.db import models


# ═══════════════════════════════════════════════════════════════════════════
#  BUSINESS LINKS MODULE  (replaces the external "Automate Links" tool)
# ═══════════════════════════════════════════════════════════════════════════


class LinkCategory(models.Model):
    """A folder of business links (Sales, Operations, HR, …)."""

    name = models.CharField(max_length=80, unique=True)
    color = models.CharField(max_length=7, default="#2563eb")
    icon = models.CharField(max_length=40, default="bi-link-45deg")
    description = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Link categories"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Link(models.Model):
    """A single business URL bookmark shared across the team."""

    title = models.CharField(max_length=200)
    url = models.URLField(max_length=500)
    description = models.CharField(max_length=255, blank=True)
    category = models.ForeignKey(LinkCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="links")
    icon = models.CharField(max_length=40, blank=True,
                            help_text="Optional Bootstrap icon class; else the category icon is used.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="links_created")
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "title"]
        indexes = [models.Index(fields=["category", "display_order"], name="link_cat_order_idx")]

    def __str__(self):
        return self.title


class LinkFavorite(models.Model):
    """Per-user favorite marker for a link."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="link_favorites")
    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "link")

    def __str__(self):
        return f"{self.user} ★ {self.link_id}"
