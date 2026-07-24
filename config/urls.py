"""config URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os

from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse


def assetlinks(request):
    """Digital Asset Links for the Android TWA app (android/ directory).

    Verifies to Android that the app at TWA_PACKAGE_NAME is allowed to open
    this site fullscreen. Fingerprints come from the TWA_FINGERPRINTS env var
    (comma-separated SHA-256 cert fingerprints) so the Play App Signing key
    can be added later without a code deploy. The default below is the local
    upload key, which makes directly-installed test APKs work out of the box.
    """
    default_fp = "C7:D8:FC:18:03:2E:42:60:15:60:AF:BA:52:39:CF:E1:B6:B1:A3:3C:DF:1E:76:BA:8C:1F:29:73:95:90:EB:FC"
    fingerprints = [
        f.strip() for f in os.environ.get("TWA_FINGERPRINTS", default_fp).split(",") if f.strip()
    ]
    return JsonResponse(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": os.environ.get("TWA_PACKAGE_NAME", "bo.kadlaginvestment.crm"),
                    "sha256_cert_fingerprints": fingerprints,
                },
            }
        ],
        safe=False,
    )


urlpatterns = [
    path("admin/", admin.site.urls),

       # ✅ only include clients once, with namespace
    path("clients/", include(("clients.urls", "clients"), namespace="clients")),

    # Android app (TWA) domain verification
    path(".well-known/assetlinks.json", assetlinks),

    # redirect root to login

    path("", lambda request: redirect("clients:login", permanent=False)),



]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
