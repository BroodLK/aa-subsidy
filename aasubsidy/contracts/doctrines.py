from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView
from django.db import models
from django.db.models import Max
from django.contrib import messages
from fittings.models import Fitting, Doctrine
from ..models import FittingRequest

class DoctrineRequestsAdminView(PermissionRequiredMixin, TemplateView):
    template_name = "admin/doctrines.html"
    permission_required = "aasubsidy.subsidy_admin"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        doctrines = (
            Doctrine.objects
            .values("name")
            .annotate(
                requested=Max(
                    "fittings__subsidy_request__requested",
                    filter=models.Q(fittings__subsidy_request__isnull=False),
                    default=0,
                )
            )
            .order_by("name")
        )

        ctx["doctrines"] = [{"name": d["name"], "requested": d["requested"] or 0} for d in doctrines]
        return ctx

    def post(self, request, *args, **kwargs):
        doctrine_name = request.POST.get("doctrine_name") or ""
        try:
            value = int(request.POST.get("requested") or "0")
        except ValueError:
            value = 0
        if not doctrine_name:
            messages.error(request, "Missing doctrine name.")
            return redirect(reverse("aasubsidy:doctrine_admin"))

        fit_ids = list(
            Fitting.objects.filter(doctrines__name=doctrine_name).values_list("id", flat=True)
        )

        existing = {fr.fitting_id: fr for fr in FittingRequest.objects.filter(fitting_id__in=fit_ids)}
        to_create, to_update = [], []
        for fid in fit_ids:
            if fid in existing:
                fr = existing[fid]
                if fr.requested != value:
                    fr.requested = value
                    to_update.append(fr)
            else:
                to_create.append(FittingRequest(fitting_id=fid, requested=value))
        if to_create:
            FittingRequest.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            FittingRequest.objects.bulk_update(to_update, ["requested"])
        messages.success(request, f"Requested stock set to {value} for doctrine {doctrine_name}.")
        return redirect(reverse("aasubsidy:doctrine_admin"))


class DoctrineRequestsDetailView(PermissionRequiredMixin, TemplateView):
    template_name = "admin/doctrine_detail.html"
    permission_required = "aasubsidy.subsidy_admin"

    def get_context_data(self, doctrine_name: str = None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        doctrine_name = doctrine_name or kwargs.get("doctrine_name") or ""
        ctx["doctrine_name"] = doctrine_name

        fittings = list(
            Fitting.objects.filter(doctrines__name=doctrine_name)
            .order_by("name", "id")
            .values("id", "name")
        )
        req_map = {
            fr["fitting_id"]: fr["requested"]
            for fr in FittingRequest.objects.filter(
                fitting_id__in=[f["id"] for f in fittings]
            ).values("fitting_id", "requested")
        }
        for f in fittings:
            f["requested"] = req_map.get(f["id"], 0)
        ctx["fittings"] = fittings
        return ctx

    def post(self, request, doctrine_name: str = None, **kwargs):
        doctrine_name = doctrine_name or kwargs.get("doctrine_name") or ""
        fit_ids = list(
            Fitting.objects.filter(doctrines__name=doctrine_name).values_list("id", flat=True)
        )
        existing = {fr.fitting_id: fr for fr in FittingRequest.objects.filter(fitting_id__in=fit_ids)}

        to_create, to_update = [], []
        for fid in fit_ids:
            key = f"requested_{fid}"
            if key not in request.POST:
                continue
            try:
                value = int(request.POST.get(key) or "0")
            except ValueError:
                value = 0
            if fid in existing:
                fr = existing[fid]
                if fr.requested != value:
                    fr.requested = value
                    to_update.append(fr)
            else:
                to_create.append(FittingRequest(fitting_id=fid, requested=value))
        if to_create:
            FittingRequest.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            FittingRequest.objects.bulk_update(to_update, ["requested"])
        messages.success(request, f"Updated requested stock for doctrine {doctrine_name}.")
        return redirect(reverse("aasubsidy:doctrine_detail", kwargs={"doctrine_name": doctrine_name}))