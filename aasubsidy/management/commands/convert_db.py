import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from aasubsidy.tasks import (
    sync_fitting_requests,
    seed_all_types_into_subsidy,
    refresh_subsidy_item_prices,
)
from aasubsidy.models import CorporateContractSubsidy
from corptools.models import CorporateContract

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run initial data imports for Subsidy module: fittings -> types -> prices -> contract subsidies"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write any changes to the DB for the contract subsidy import (still runs syncing tasks).",
        )

    def handle(self, *args, **options):
        dry_run: bool = bool(options.get("dry_run"))

        self.stdout.write(self.style.MIGRATE_HEADING("Step 1/4: Sync fitting requests (wait to finish)…"))
        try:
            # Run synchronously to completion
            res1 = sync_fitting_requests.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
            self.stdout.write(self.style.SUCCESS(f"Fitting requests synced: {res1}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed syncing fitting requests: {e}"))
            raise

        self.stdout.write(self.style.MIGRATE_HEADING("Step 2/4: Seed all EveTypes into SubsidyItemPrice (wait to finish)…"))
        try:
            res2 = seed_all_types_into_subsidy.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
            self.stdout.write(self.style.SUCCESS(f"Seed complete: {res2}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed seeding types: {e}"))
            raise

        self.stdout.write(self.style.MIGRATE_HEADING("Step 3/4: Queue price refresh (not waiting to finish)…"))
        try:
            # Execute the wrapper task to enqueue update_all_prices; do not block on the inner task
            res3 = refresh_subsidy_item_prices.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
            self.stdout.write(self.style.SUCCESS(f"Price refresh queued: {res3}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to queue price refresh: {e}"))
            raise

        self.stdout.write(self.style.MIGRATE_HEADING("Step 4/4: Import Corporate Contracts into Subsidy (upsert)…"))
        try:
            created, updated, exempted = self._import_contracts_into_subsidy(dry_run=dry_run)
            suffix = " (dry-run)" if dry_run else ""
            self.stdout.write(self.style.SUCCESS(f"Contracts imported{suffix}: created={created}, updated={updated}, exempted={exempted}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed importing contracts: {e}"))
            raise

        self.stdout.write(self.style.SUCCESS("All done."))

    def _import_contracts_into_subsidy(self, *, dry_run: bool = False, chunk_size: int = 1000):
        """
        Upsert CorporateContractSubsidy records for all CorporateContract rows.
        - Create missing subsidy rows.
        - Update review_status, subsidy_amount, paid, reason from CorporateContract fields.
        - Apply 'exempt' flag consistency similar to existing logic: mark deleted and not expired as exempt.
        """
        qs = CorporateContract.objects.all().only(
            "id", "status", "date_expired", "review_status", "subsidy_amount", "paid", "reason"
        )

        ids = list(qs.values_list("id", flat=True))
        total = len(ids)
        created = 0
        updated = 0

        existing_map = {
            s.contract_id: s
            for s in CorporateContractSubsidy.objects.filter(contract_id__in=ids).only(
                "id", "contract_id", "review_status", "subsidy_amount", "paid", "reason", "exempt"
            )
        }

        to_create = []
        to_update = []

        now = timezone.now()

        for i in range(0, total, chunk_size):
            batch = list(
                CorporateContract.objects.filter(id__in=ids[i : i + chunk_size]).only(
                    "id", "status", "date_expired", "review_status", "subsidy_amount", "paid", "reason"
                )
            )
            for cc in batch:
                existing = existing_map.get(cc.id)
                exempt_flag = False
                if cc.status == "deleted" and cc.date_expired is not None and cc.date_expired > now:
                    exempt_flag = True

                if existing is None:
                    obj = CorporateContractSubsidy(
                        contract_id=cc.id,
                        review_status=int(cc.review_status or 0),
                        subsidy_amount=Decimal(cc.subsidy_amount or 0),
                        paid=bool(cc.paid or False),
                        reason=str(cc.reason or ""),
                        exempt=exempt_flag,
                    )
                    to_create.append(obj)
                    created += 1
                else:
                    changed = False

                    new_review_status = int(cc.review_status or 0)
                    if existing.review_status != new_review_status:
                        existing.review_status = new_review_status
                        changed = True

                    new_subsidy_amount = Decimal(cc.subsidy_amount or 0)
                    if existing.subsidy_amount != new_subsidy_amount:
                        existing.subsidy_amount = new_subsidy_amount
                        changed = True

                    new_paid = bool(cc.paid or False)
                    if existing.paid != new_paid:
                        existing.paid = new_paid
                        changed = True

                    new_reason = str(cc.reason or "")
                    if existing.reason != new_reason:
                        existing.reason = new_reason
                        changed = True

                    if existing.exempt != exempt_flag:
                        existing.exempt = exempt_flag
                        changed = True

                    if changed:
                        to_update.append(existing)
                        updated += 1

        if dry_run:
            return created, updated, sum(1 for obj in to_create if obj.exempt) + sum(1 for obj in to_update if obj.exempt)

        # Persist in chunks
        if to_create:
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_create(to_create, ignore_conflicts=True)

        if to_update:
            # Update only the mapped fields
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_update(
                    to_update, ["review_status", "subsidy_amount", "paid", "reason", "exempt"]
                )

        exempted = CorporateContractSubsidy.objects.filter(
            contract_id__in=ids, exempt=True
        ).count()

        return created, updated, exempted

