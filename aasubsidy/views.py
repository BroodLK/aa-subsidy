"""App Views"""

# Django
from django.contrib.auth.decorators import login_required, permission_required
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse
from django.shortcuts import render


@login_required
@permission_required("aasubsidy.basic_access")
def index(request: WSGIRequest) -> HttpResponse:
    return render(request, "contracts/summary.html")
