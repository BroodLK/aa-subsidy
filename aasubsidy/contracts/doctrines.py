from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView
from django.http import JsonResponse
from django.db import models
from django.db.models import Max, Q
from django.contrib import messages
from fittings.models import Fitting, Doctrine
from ..models import FittingRequest, DoctrineSystem, DoctrineLocation
from eveuniverse.models import EveEntity, EveSolarSystem, EveStation
from corptools.models import MapSystem, EveLocation

def location_search(request):
    query = request.GET.get("q", "")
    category = request.GET.get("category", "solar_system")
    if len(query) < 3:
        return JsonResponse({"results": []})
    
    # Use a dictionary to keep unique entities by their EVE ID
    results_map = {} # eve_id -> name
    
    # 1. Search EveEntity (most flexible)
    entities_qs = EveEntity.objects.filter(Q(name__icontains=query))
    if category:
        entities_qs = entities_qs.filter(category=category)
    else:
        entities_qs = entities_qs.filter(category__in=["solar_system"])
    
    entities = entities_qs.only("id", "name")[:20]
    for e in entities:
        results_map[e.id] = e.name
    
    # 2. Search specific models as fallback/supplement
    if len(results_map) < 20 and (not category or category == "solar_system"):
        systems = EveSolarSystem.objects.filter(name__icontains=query).only("id", "name")[:20]
        for s in systems:
            if s.id not in results_map:
                results_map[s.id] = s.name
                
    # 3. Search Corptools models
    if len(results_map) < 20 and (not category or category == "solar_system"):
        ct_systems = MapSystem.objects.filter(name__icontains=query).only("system_id", "name")[:20]
        for s in ct_systems:
            if s.system_id not in results_map:
                results_map[s.system_id] = s.name

    results = [{"id": eid, "name": name} for eid, name in results_map.items()]
    return JsonResponse({"results": results[:20]})

class DoctrineRequestsAdminView(PermissionRequiredMixin, TemplateView):
    template_name = "admin/doctrines.html"
    permission_required = "aasubsidy.subsidy_admin"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        system_id = self.request.GET.get("system_id")
        systems_qs = DoctrineSystem.objects.all().order_by("name")
        if not system_id:
            first_sys = systems_qs.filter(is_active=True).first() or systems_qs.first()
            system_id = first_sys.id if first_sys else None

        ctx["systems"] = systems_qs
        ctx["selected_system"] = DoctrineSystem.objects.filter(id=system_id).first() if system_id else None
        ctx["selected_system_id"] = int(system_id) if system_id else None

        doctrines = (
            Doctrine.objects
            .values("name")
            .annotate(
                requested=Max(
                    "fittings__subsidy_requests__requested",
                    filter=models.Q(fittings__subsidy_requests__system_id=system_id),
                    default=0,
                )
            )
            .order_by("name")
        )

        ctx["doctrines"] = [{"name": d["name"], "requested": d["requested"] or 0} for d in doctrines]
        return ctx

    def post(self, request, *args, **kwargs):
        if "create_system" in request.POST:
            name = request.POST.get("system_name")
            eve_id = request.POST.get("system_eve_id")
            if name:
                system, created = DoctrineSystem.objects.get_or_create(name=name)
                if eve_id:
                    try:
                        entity, _ = EveEntity.objects.get_or_create_esi(id=int(eve_id))
                        DoctrineLocation.objects.get_or_create(system=system, location=entity)
                        messages.success(request, f"System {name} created with location {entity.name}.")
                    except Exception as e:
                        messages.warning(request, f"System {name} created, but failed to add location: {e}")
                else:
                    messages.success(request, f"System {name} created.")
            return redirect(reverse("aasubsidy:doctrine_admin"))

        if "delete_system" in request.POST:
            sid = request.POST.get("system_id")
            if sid:
                DoctrineSystem.objects.filter(id=sid).delete()
                messages.success(request, "System deleted.")
            return redirect(reverse("aasubsidy:doctrine_admin"))

        if "toggle_active" in request.POST:
            sid = request.POST.get("system_id")
            if sid:
                sys = DoctrineSystem.objects.get(id=sid)
                sys.is_active = not sys.is_active
                sys.save(update_fields=["is_active"])
                state = "activated" if sys.is_active else "deactivated"
                messages.success(request, f"System {sys.name} {state}.")
            return redirect(f"{reverse('aasubsidy:doctrine_admin')}?system_id={sid}")

        if "remove_location" in request.POST:
            sid = request.POST.get("system_id")
            loc_id = request.POST.get("location_id")
            if loc_id:
                DoctrineLocation.objects.filter(id=loc_id).delete()
                messages.success(request, "Location removed.")
            return redirect(f"{reverse('aasubsidy:doctrine_admin')}?system_id={sid}")

        doctrine_name = request.POST.get("doctrine_name") or ""
        system_id = request.POST.get("system_id")
        try:
            value = int(request.POST.get("requested") or "0")
        except ValueError:
            value = 0

        if not doctrine_name or not system_id:
            messages.error(request, "Missing doctrine name or system.")
            return redirect(reverse("aasubsidy:doctrine_admin"))

        fit_ids = list(
            Fitting.objects.filter(doctrines__name=doctrine_name).values_list("id", flat=True)
        )

        existing = {fr.fitting_id: fr for fr in FittingRequest.objects.filter(fitting_id__in=fit_ids, system_id=system_id)}
        to_create, to_update = [], []
        for fid in fit_ids:
            if fid in existing:
                fr = existing[fid]
                if fr.requested != value:
                    fr.requested = value
                    to_update.append(fr)
            else:
                to_create.append(FittingRequest(fitting_id=fid, requested=value, system_id=system_id))
        if to_create:
            FittingRequest.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            FittingRequest.objects.bulk_update(to_update, ["requested"])
        messages.success(request, f"Requested stock set to {value} for doctrine {doctrine_name} in selected system.")
        return redirect(f"{reverse('aasubsidy:doctrine_admin')}?system_id={system_id}")


class DoctrineRequestsDetailView(PermissionRequiredMixin, TemplateView):
    template_name = "admin/doctrine_detail.html"
    permission_required = "aasubsidy.subsidy_admin"

    def get_context_data(self, doctrine_name: str = None, **kwargs):
        ctx = super().get_context_data(**kwargs)
        doctrine_name = doctrine_name or kwargs.get("doctrine_name") or ""
        ctx["doctrine_name"] = doctrine_name

        system_id = self.request.GET.get("system_id")
        systems_qs = DoctrineSystem.objects.filter(is_active=True).order_by("name")
        if not system_id:
            first_sys = systems_qs.first()
            system_id = first_sys.id if first_sys else None

        ctx["systems"] = systems_qs
        ctx["selected_system_id"] = int(system_id) if system_id else None

        fittings = list(
            Fitting.objects.filter(doctrines__name=doctrine_name)
            .order_by("name", "id")
            .values("id", "name")
        )
        req_map = {
            fr["fitting_id"]: fr["requested"]
            for fr in FittingRequest.objects.filter(
                fitting_id__in=[f["id"] for f in fittings],
                system_id=system_id
            ).values("fitting_id", "requested")
        }
        for f in fittings:
            f["requested"] = req_map.get(f["id"], 0)
        ctx["fittings"] = fittings
        return ctx

    def post(self, request, doctrine_name: str = None, **kwargs):
        doctrine_name = doctrine_name or kwargs.get("doctrine_name") or ""
        system_id = request.POST.get("system_id")
        if not system_id:
            messages.error(request, "No system selected.")
            return redirect(reverse("aasubsidy:doctrine_detail", kwargs={"doctrine_name": doctrine_name}))

        fit_ids = list(
            Fitting.objects.filter(doctrines__name=doctrine_name).values_list("id", flat=True)
        )
        existing = {fr.fitting_id: fr for fr in FittingRequest.objects.filter(fitting_id__in=fit_ids, system_id=system_id)}

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
                to_create.append(FittingRequest(fitting_id=fid, requested=value, system_id=system_id))
        if to_create:
            FittingRequest.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            FittingRequest.objects.bulk_update(to_update, ["requested"])
        messages.success(request, f"Updated requested stock for doctrine {doctrine_name} in selected system.")
        return redirect(f"{reverse('aasubsidy:doctrine_detail', kwargs={'doctrine_name': doctrine_name})}?system_id={system_id}")