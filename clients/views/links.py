"""Business Links views — replaces the external "Automate Links" tool.

A shared, categorized repository of business URLs. Admins manage categories and
any link; employees add links and manage their own; everyone can browse and
favorite. Reuses CRM auth, the design system, and nav.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Link, LinkCategory, LinkFavorite


def _role(request):
    emp = getattr(request.user, "employee", None)
    return getattr(emp, "role", "") if emp else ""


def _is_admin(request):
    return request.user.is_superuser or _role(request) == "admin"


def _can_edit_link(request, link):
    return _is_admin(request) or link.created_by_id == request.user.id


def _fav_ids(request):
    return set(
        LinkFavorite.objects.filter(user=request.user).values_list("link_id", flat=True)
    )


@login_required
def links_dashboard(request):
    q = (request.GET.get("q") or "").strip()
    links = Link.objects.filter(is_active=True).select_related("category", "created_by")
    if q:
        links = links.filter(
            Q(title__icontains=q) | Q(url__icontains=q)
            | Q(description__icontains=q) | Q(category__name__icontains=q)
        )

    fav_ids = _fav_ids(request)

    if q:
        # Search mode: flat result list.
        results = list(links[:200])
        for link in results:
            link.is_fav = link.id in fav_ids
            link.can_edit = _can_edit_link(request, link)
        return render(request, "links/search.html", {
            "page_title": "Business Links",
            "q": q,
            "results": results,
            "is_admin": _is_admin(request),
            "categories": LinkCategory.objects.filter(is_active=True),
        })

    # Card mode: categories with a preview of their links.
    categories = list(
        LinkCategory.objects.filter(is_active=True)
        .annotate(link_count=Count("links", filter=Q(links__is_active=True)))
    )
    by_cat = {}
    for link in links:
        by_cat.setdefault(link.category_id, []).append(link)
    for cat in categories:
        preview = by_cat.get(cat.id, [])
        cat.preview_links = preview[:5]

    favorites = [l for l in links if l.id in fav_ids]
    for link in favorites:
        link.is_fav = True
        link.can_edit = _can_edit_link(request, link)

    return render(request, "links/dashboard.html", {
        "page_title": "Business Links",
        "categories": categories,
        "favorites": favorites,
        "uncategorized": by_cat.get(None, [])[:5],
        "uncategorized_count": len(by_cat.get(None, [])),
        "is_admin": _is_admin(request),
    })


@login_required
def links_category(request, cat_id):
    category = get_object_or_404(LinkCategory, pk=cat_id)
    q = (request.GET.get("q") or "").strip()
    links = category.links.filter(is_active=True).select_related("created_by")
    if q:
        links = links.filter(Q(title__icontains=q) | Q(url__icontains=q) | Q(description__icontains=q))
    fav_ids = _fav_ids(request)
    link_list = list(links)
    for link in link_list:
        link.is_fav = link.id in fav_ids
        link.can_edit = _can_edit_link(request, link)
    return render(request, "links/category.html", {
        "page_title": category.name,
        "category": category,
        "links": link_list,
        "q": q,
        "categories": LinkCategory.objects.filter(is_active=True),
        "is_admin": _is_admin(request),
    })


@login_required
@require_POST
def link_create(request):
    title = (request.POST.get("title") or "").strip()
    url = (request.POST.get("url") or "").strip()
    if not title or not url:
        messages.error(request, "A link needs a title and a URL.")
        return redirect(request.META.get("HTTP_REFERER") or "clients:links_dashboard")
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

    cat_id = request.POST.get("category")
    category = LinkCategory.objects.filter(pk=cat_id).first() if cat_id and cat_id.isdigit() else None
    order = Link.objects.filter(category=category).count()
    Link.objects.create(
        title=title[:200],
        url=url[:500],
        description=(request.POST.get("description") or "")[:255],
        category=category,
        icon=(request.POST.get("icon") or "")[:40],
        created_by=request.user,
        display_order=order,
    )
    messages.success(request, f"Added “{title}”.")
    return redirect(request.META.get("HTTP_REFERER") or "clients:links_dashboard")


@login_required
@require_POST
def link_update(request, link_id):
    link = get_object_or_404(Link, pk=link_id)
    if not _can_edit_link(request, link):
        return HttpResponseForbidden("You cannot edit this link.")
    title = (request.POST.get("title") or "").strip()
    url = (request.POST.get("url") or "").strip()
    if title:
        link.title = title[:200]
    if url:
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        link.url = url[:500]
    link.description = (request.POST.get("description") or "")[:255]
    link.icon = (request.POST.get("icon") or "")[:40]
    cat_id = request.POST.get("category")
    link.category = LinkCategory.objects.filter(pk=cat_id).first() if cat_id and cat_id.isdigit() else None
    link.save()
    messages.success(request, "Link updated.")
    return redirect(request.META.get("HTTP_REFERER") or "clients:links_dashboard")


@login_required
@require_POST
def link_delete(request, link_id):
    link = get_object_or_404(Link, pk=link_id)
    if not _can_edit_link(request, link):
        return HttpResponseForbidden("You cannot delete this link.")
    link.delete()
    messages.success(request, "Link deleted.")
    return redirect(request.META.get("HTTP_REFERER") or "clients:links_dashboard")


@login_required
@require_POST
def link_favorite(request, link_id):
    link = get_object_or_404(Link, pk=link_id)
    fav, created = LinkFavorite.objects.get_or_create(user=request.user, link=link)
    if not created:
        fav.delete()
    return redirect(request.META.get("HTTP_REFERER") or "clients:links_dashboard")


@login_required
@require_POST
def link_move(request, link_id):
    """Nudge a link up or down within its category (display order)."""
    link = get_object_or_404(Link, pk=link_id)
    if not _can_edit_link(request, link):
        return HttpResponseForbidden("You cannot reorder this link.")
    direction = request.POST.get("dir")
    siblings = list(Link.objects.filter(category=link.category, is_active=True).order_by("display_order", "title"))
    idx = next((i for i, l in enumerate(siblings) if l.id == link.id), None)
    if idx is not None:
        swap = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap < len(siblings):
            other = siblings[swap]
            link.display_order, other.display_order = other.display_order, link.display_order
            # Ensure distinct values if they collided.
            if link.display_order == other.display_order:
                other.display_order += 1
            link.save(update_fields=["display_order"])
            other.save(update_fields=["display_order"])
    return redirect(request.META.get("HTTP_REFERER") or "clients:links_dashboard")


@login_required
@require_POST
def link_category_create(request):
    """Quick-create a link category from the Add Link dropdown (AJAX JSON)."""
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    order = LinkCategory.objects.count()
    cat, _created = LinkCategory.objects.get_or_create(
        name=name[:80],
        defaults={
            "color": (request.POST.get("color") or "#2563eb")[:7],
            "icon": (request.POST.get("icon") or "bi-link-45deg")[:40],
            "display_order": order,
            "created_by": request.user,
        },
    )
    return JsonResponse({"ok": True, "id": cat.id, "name": cat.name, "color": cat.color})


@login_required
def link_categories(request):
    if not _is_admin(request):
        return HttpResponseForbidden("Admins only.")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = (request.POST.get("name") or "").strip()
            if name:
                order = LinkCategory.objects.count()
                LinkCategory.objects.get_or_create(
                    name=name[:80],
                    defaults={
                        "color": (request.POST.get("color") or "#2563eb")[:7],
                        "icon": (request.POST.get("icon") or "bi-link-45deg")[:40],
                        "description": (request.POST.get("description") or "")[:255],
                        "display_order": order,
                        "created_by": request.user,
                    },
                )
                messages.success(request, f"Category “{name}” added.")
        elif action == "toggle":
            cat = LinkCategory.objects.filter(pk=request.POST.get("id")).first()
            if cat:
                cat.is_active = not cat.is_active
                cat.save(update_fields=["is_active", "updated_at"])
        elif action == "delete":
            LinkCategory.objects.filter(pk=request.POST.get("id")).delete()
            messages.success(request, "Category deleted.")
        return redirect("clients:link_categories")

    categories = LinkCategory.objects.annotate(link_count=Count("links"))
    return render(request, "links/categories.html", {
        "page_title": "Link Categories",
        "categories": categories,
        "is_admin": True,
    })
