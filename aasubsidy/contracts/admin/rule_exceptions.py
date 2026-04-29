from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View, TemplateView

from ...models import DoctrineItemRule, DoctrineSubstitutionRule, DoctrineQuantityTolerance
from fittings.models import Fitting


class RuleExceptionsView(PermissionRequiredMixin, TemplateView):
    """View to manage all manually created rule exceptions."""

    permission_required = "aasubsidy.manage_doctrines"
    template_name = "admin/rule_exceptions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get all manually created rules grouped by fitting
        fittings_with_rules = {}

        # Collect item rules (optional, ignored)
        for rule in DoctrineItemRule.objects.select_related('fitting', 'type').filter(
            rule_kind__in=['optional', 'ignore']
        ).order_by('fitting__name', 'type__name'):
            fitting_id = rule.fitting_id
            if fitting_id not in fittings_with_rules:
                fittings_with_rules[fitting_id] = {
                    'fitting': rule.fitting,
                    'item_rules': [],
                    'substitution_rules': [],
                    'quantity_tolerances': [],
                }
            fittings_with_rules[fitting_id]['item_rules'].append(rule)

        # Collect substitution rules
        for rule in DoctrineSubstitutionRule.objects.select_related(
            'fitting', 'expected_type', 'allowed_type'
        ).order_by('fitting__name', 'expected_type__name'):
            fitting_id = rule.fitting_id
            if fitting_id not in fittings_with_rules:
                fittings_with_rules[fitting_id] = {
                    'fitting': rule.fitting,
                    'item_rules': [],
                    'substitution_rules': [],
                    'quantity_tolerances': [],
                }
            fittings_with_rules[fitting_id]['substitution_rules'].append(rule)

        # Collect quantity tolerances
        for tolerance in DoctrineQuantityTolerance.objects.select_related(
            'fitting', 'type'
        ).order_by('fitting__name', 'type__name'):
            fitting_id = tolerance.fitting_id
            if fitting_id not in fittings_with_rules:
                fittings_with_rules[fitting_id] = {
                    'fitting': tolerance.fitting,
                    'item_rules': [],
                    'substitution_rules': [],
                    'quantity_tolerances': [],
                }
            fittings_with_rules[fitting_id]['quantity_tolerances'].append(tolerance)

        context['fittings_with_rules'] = dict(sorted(
            fittings_with_rules.items(),
            key=lambda x: x[1]['fitting'].name.lower()
        ))

        return context


@method_decorator(csrf_exempt, name="dispatch")
class DeleteRuleView(PermissionRequiredMixin, View):
    """Delete a specific rule exception."""

    permission_required = "aasubsidy.manage_doctrines"

    @transaction.atomic
    def post(self, request):
        rule_type = request.POST.get('rule_type')
        rule_id = request.POST.get('rule_id')

        if not rule_type or not rule_id:
            messages.error(request, "Invalid request.")
            return redirect('aasubsidy:rule_exceptions')

        try:
            rule_id = int(rule_id)

            if rule_type == 'item':
                rule = DoctrineItemRule.objects.get(pk=rule_id)
                rule_desc = f"{rule.type.name} ({rule.get_rule_kind_display()})"
                rule.delete()
            elif rule_type == 'substitution':
                rule = DoctrineSubstitutionRule.objects.get(pk=rule_id)
                rule_desc = f"{rule.expected_type.name} → {rule.allowed_type.name if rule.allowed_type else 'profile variants'}"
                rule.delete()
            elif rule_type == 'quantity':
                tolerance = DoctrineQuantityTolerance.objects.get(pk=rule_id)
                rule_desc = f"{tolerance.type.name} quantity tolerance"
                tolerance.delete()
            else:
                messages.error(request, "Unknown rule type.")
                return redirect('aasubsidy:rule_exceptions')

            messages.success(request, f"Deleted rule: {rule_desc}")

        except (ValueError, DoctrineItemRule.DoesNotExist, DoctrineSubstitutionRule.DoesNotExist, DoctrineQuantityTolerance.DoesNotExist):
            messages.error(request, "Rule not found.")

        return redirect('aasubsidy:rule_exceptions')
