import json
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def specification(request):
    return JsonResponse(
        json.loads((settings.BASE_DIR / "docs/openapi.json").read_text())
    )


@login_required
@require_GET
def explorer(request):
    return render(request, "integrations/explorer.html", {"nav": "integrations"})
