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

import csv
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run initial data imports for Subsidy module: fittings -> types -> prices -> contract subsidies"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Do not persist writes.")
        parser.add_argument("--chunk-size", type=int, default=1000, help="Batch size for upserts.")
        parser.add_argument(
            "--contracts-dump",
            type=str,
            default="",
            help="Path to contracts dump file (CSV or JSON). Columns/keys required: contract_id, review_status, subsidy_amount, paid, reason",
        )
        parser.add_argument(
            "--skip-sync",
            action="store_true",
            help="Skip steps 1-3 (fitting sync, seed types, queue price refresh) and only import contracts.",
        )

    def handle(self, *args, **options):
        dry_run: bool = bool(options.get("dry_run"))
        chunk_size: int = int(options.get("chunk_size") or 1000)
        dump_path: str = (options.get("contracts_dump") or "").strip()
        skip_sync: bool = bool(options.get("skip_sync"))

        if not skip_sync:
            self.stdout.write(self.style.MIGRATE_HEADING("Step 1/4: Sync fitting requests (wait)…"))
            res1 = sync_fitting_requests.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
            self.stdout.write(self.style.SUCCESS(f"Fitting requests synced: {res1}"))

            self.stdout.write(self.style.MIGRATE_HEADING("Step 2/4: Seed all EveTypes into SubsidyItemPrice (wait)…"))
            res2 = seed_all_types_into_subsidy.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
            self.stdout.write(self.style.SUCCESS(f"Seed complete: {res2}"))

            self.stdout.write(self.style.MIGRATE_HEADING("Step 3/4: Queue price refresh (no wait for inner task)…"))
            res3 = refresh_subsidy_item_prices.apply(args=[], kwargs={}).get(disable_sync_subtasks=False)
            self.stdout.write(self.style.SUCCESS(f"Price refresh queued: {res3}"))

        self.stdout.write(self.style.MIGRATE_HEADING("Step 4/4: Import Corporate Contracts into Subsidy (upsert)…"))

        if not dump_path:
            self.stdout.write(self.style.ERROR("Missing --contracts-dump. Provide a CSV or JSON dump file."))
            return

        path = Path(dump_path)
        if not path.exists():
            self.stdout.write(self.style.ERROR(f"Dump file not found: {path}"))
            return

        # Load dump rows
        rows = self._load_dump(path)
        if not rows:
            self.stdout.write(self.style.WARNING("No rows loaded from dump; nothing to do."))
            return

        created, updated = self._upsert_from_dump(rows, chunk_size=chunk_size, dry_run=dry_run)
        suffix = " (dry-run)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"Contracts imported from dump{suffix}: created={created}, updated={updated}"))
        self.stdout.write(self.style.SUCCESS("All done."))

    def _load_dump(self, path: Path) -> list[dict]:
        ext = path.suffix.lower()
        if ext == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # allow {"rows": [...]}
                    data = data.get("rows") or []
                if not isinstance(data, list):
                    self.stdout.write(self.style.ERROR("JSON dump must be a list or have 'rows' list."))
                    return []
                return [self._normalize_row(d) for d in data if isinstance(d, dict)]
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to read JSON: {e}"))
                return []
        # default to CSV
        try:
            out = []
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    out.append(self._normalize_row(r))
            return out
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to read CSV: {e}"))
            return []

    def _normalize_row(self, row: dict) -> dict:
        # expected keys: contract_id, review_status, subsidy_amount, paid, reason
        def to_int(x, default=0):
            try:
                return int(x)
            except Exception:
                return default

        def to_dec(x):
            try:
                return Decimal(str(x))
            except Exception:
                return Decimal("0")

        def to_bool(x):
            s = str(x).strip().lower()
            return s in ("1", "true", "t", "yes", "y")

        return {
            "contract_id": to_int(row.get("contract_id")),
            "review_status": to_int(row.get("review_status"), 0),
            "subsidy_amount": to_dec(row.get("subsidy_amount")),
            "paid": to_bool(row.get("paid")),
            "reason": (row.get("reason") or "").strip(),
        }

    def _upsert_from_dump(self, rows: list[dict], *, chunk_size: int, dry_run: bool):
        # Build contract_id -> payload map; last one wins if duplicates present in dump
        payloads: dict[int, dict] = {}
        for r in rows:
            cid = int(r.get("contract_id") or 0)
            if cid > 0:
                payloads[cid] = r

        if not payloads:
            return 0, 0

        # Find CorporateContract PKs by contract_id
        cid_list = list(payloads.keys())
        cc_qs = CorporateContract.objects.filter(
            contract_id__in=cid_list, corporation_id=1
        ).only("id", "contract_id")
        cc_map = {cc.contract_id: cc.id for cc in cc_qs}

        missing = [cid for cid in cid_list if cid not in cc_map]
        if missing:
            logger.warning("Contracts in dump not found in CorporateContract: %s (showing up to 20)", missing[:20])

        # Prepare existing subsidy mapping
        existing = {
            s.contract_id: s
            for s in CorporateContractSubsidy.objects.filter(contract_id__in=cc_map.values()).only(
                "id", "contract_id", "review_status", "subsidy_amount", "paid", "reason"
            )
        }

        to_create: list[CorporateContractSubsidy] = []
        to_update: list[CorporateContractSubsidy] = []
        created = 0
        updated = 0

        items = [(cid, payloads[cid]) for cid in cid_list if cid in cc_map]
        for i in range(0, len(items), chunk_size):
            batch = items[i : i + chunk_size]
            for contract_id_val, data in batch:
                cc_pk = cc_map[contract_id_val]
                cur = existing.get(cc_pk)
                if cur is None:
                    obj = CorporateContractSubsidy(
                        contract_id=cc_pk,
                        review_status=data["review_status"],
                        subsidy_amount=data["subsidy_amount"],
                        paid=data["paid"],
                        reason=data["reason"],
                    )
                    to_create.append(obj)
                    created += 1
                else:
                    changed = False
                    if cur.review_status != data["review_status"]:
                        cur.review_status = data["review_status"]; changed = True
                    if cur.subsidy_amount != data["subsidy_amount"]:
                        cur.subsidy_amount = data["subsidy_amount"]; changed = True
                    if cur.paid != data["paid"]:
                        cur.paid = data["paid"]; changed = True
                    if cur.reason != data["reason"]:
                        cur.reason = data["reason"]; changed = True
                    if changed:
                        to_update.append(cur)
                        updated += 1

        if dry_run:
            return created, updated

        if to_create:
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            with transaction.atomic():
                CorporateContractSubsidy.objects.bulk_update(
                    to_update, ["review_status", "subsidy_amount", "paid", "reason"]
                )
        return created, updated
