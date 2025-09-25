import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
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
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=1000,
            help="Batch size for upserts.",
        )
        parser.add_argument(
            "--external-table",
            type=str,
            default="corptools.corporatecontracts",
            help="Fully-qualified source table for custom fields (schema.table).",
        )

    def handle(self, *args, **options):
        dry_run: bool = bool(options.get("dry_run"))
        chunk_size: int = int(options.get("chunk_size") or 1000)
        external_table: str = str(options.get("external_table") or "corptools.corporatecontracts")

        # 1) sync_fitting_requests (wait)
        self.stdout.write(self.style.MIGRATE_HEADING("Step 1/4: Sync fitting requests (wait)…"))
        res1 = sync_fitting_requests.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
        self.stdout.write(self.style.SUCCESS(f"Fitting requests synced: {res1}"))

        # 2) seed_all_types_into_subsidy (wait)
        self.stdout.write(self.style.MIGRATE_HEADING("Step 2/4: Seed all EveTypes into SubsidyItemPrice (wait)…"))
        res2 = seed_all_types_into_subsidy.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
        self.stdout.write(self.style.SUCCESS(f"Seed complete: {res2}"))

        # 3) refresh_subsidy_item_prices (do not wait for inner update)
        self.stdout.write(self.style.MIGRATE_HEADING("Step 3/4: Queue price refresh (no wait for inner task)…"))
        res3 = refresh_subsidy_item_prices.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
        self.stdout.write(self.style.SUCCESS(f"Price refresh queued: {res3}"))

        # 4) import/merge contract subsidy data
        self.stdout.write(self.style.MIGRATE_HEADING("Step 4/4: Import Corporate Contracts into Subsidy (upsert)…"))
        created, updated, exempted = self._import_contracts_into_subsidy(
            external_table=external_table, dry_run=dry_run, chunk_size=chunk_size
        )
        suffix = " (dry-run)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"Contracts imported{suffix}: created={created}, updated={updated}, exempted={exempted}"
        ))
        self.stdout.write(self.style.SUCCESS("All done."))

    def _fetch_custom_contract_fields(self, contract_ids, external_table: str):
        """
        Fetch custom fields from the given external table via SQL:
          review_status (int), subsidy_amount (decimal), paid (bool/int), reason (text)

        Returns dict: {pk_id: (review_status, subsidy_amount, paid, reason)}

        Notes:
        - Falls back silently if SELECT is not permitted, returning empty dict.
        - Expects to JOIN by primary key id equality between Django model table and external table.
        """
        if not contract_ids:
            return {}

        results = {}
        BATCH = 1000

        try:
            with connection.cursor() as cursor:
                for i in range(0, len(contract_ids), BATCH):
                    batch = contract_ids[i : i + BATCH]
                    placeholders = ",".join(["%s"] * len(batch))
                    sql = f"""
                        SELECT cc.id AS pk_id,
                               ext.review_status,
                               ext.subsidy_amount,
                               ext.paid,
                               ext.reason
                        FROM corptools_corporatecontract cc
                        JOIN {external_table} ext ON ext.id = cc.id
                        WHERE cc.id IN ({placeholders})
                    """
                    cursor.execute(sql, batch)
                    for row in cursor.fetchall():
                        pk_id = int(row[0])
                        review_status = int(row[1] or 0)
                        try:
                            subsidy_amount = Decimal(row[2] or 0)
                        except Exception:
                            subsidy_amount = Decimal(0)
                        paid = bool(row[3] or False)
                        reason = str(row[4] or "")
                        results[pk_id] = (review_status, subsidy_amount, paid, reason)
        except Exception as e:
            # Permission or table errors: warn and proceed with empty results
            logger.warning("Skipping external custom field import due to error: %s", e)
            return {}

        return results

    def _import_contracts_into_subsidy(self, *, external_table: str, dry_run: bool, chunk_size: int):
        """
        Upsert CorporateContractSubsidy for all CorporateContract rows.
        - Create missing subsidy rows.
        - Update review_status, subsidy_amount, paid, reason from external source table via raw SQL when accessible.
        - Maintain exempt: deleted and not yet expired => exempt=True.
        """
        qs = CorporateContract.objects.all().only("id", "status", "date_expired")
        ids = list(qs.values_list("id", flat=True))
        total = len(ids)

        existing_map = {
            s.contract_id: s
            for s in CorporateContractSubsidy.objects.filter(contract_id__in=ids).only(
                "id", "contract_id", "review_status", "subsidy_amount", "paid", "reason", "exempt"
            )
        }

        now = timezone.now()
        created = 0
        updated = 0
        to_create = []
        to_update = []

        # Try to load custom fields; if denied, returns {}
        custom_map = self._fetch_custom_contract_fields(ids, external_table)

        for i in range(0, total, chunk_size):
            batch = list(
                CorporateContract.objects.filter(id__in=ids[i : i + chunk_size]).only(
                    "id", "status", "date_expired"
                )
            )
            for cc in batch:
                existing = existing_map.get(cc.id)

                # Defaults if custom fields are unavailable for this contract
                review_status, subsidy_amount, paid, reason = custom_map.get(
                    cc.id, (0, Decimal("0"), False, "")
                )

                exempt_flag = cc.status == "deleted" and cc.date_expired is not None and cc.date_expired > now

                if existing is None:
                    obj = CorporateContractSubsidy(
                        contract_id=cc.id,
                        review_status=review_status,
                        subsidy_amount=subsidy_amount,
                        paid=paid,
                        reason=reason,
                        exempt=exempt_flag,
                    )
                    to_create.append(obj)
                    created += 1
                else:
                    changed = False
                    if existing.review_status != review_status:
                        existing.review_status = review_status
                        changed = True
                    if existing.subsidy_amount != subsidy_amount:
                        existing.subsidy_amount = subsidy_amount
                        changed = True
                    if existing.paid != paid:
                        existing.paid = paid
                        changed = True
                    if existing.reason != reason:
                        existing.reason = reason
                        changed = True
                    if existing.exempt != exempt_flag:
                        existing.exempt = exempt_flag
                        changed = True
                    if changed:
                        to_update.append(existing)
                        updated += 1

        if dry_run:
            exempted = sum(1 for obj in to_create if obj.exempt) + sum(1 for obj in to_update if obj.exempt)
            return created, updated, exempted

        if to_create:
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_create(to_create, ignore_conflicts=True)

        if to_update:
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_update(
                    to_update, ["review_status", "subsidy_amount", "paid", "reason", "exempt"]
                )

        exempted = CorporateContractSubsidy.objects.filter(contract_id__in=ids, exempt=True).count()
        return created, updated, exempted
