from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from corptools.models import CorporateContract

from aasubsidy.contracts.filters import apply_contract_exclusions
from aasubsidy.contracts.matching import match_contracts
from aasubsidy.models import SubsidyConfig


class Command(BaseCommand):
    help = "Backfill DoctrineMatchResult for historical contracts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--corporation-id",
            type=int,
            default=None,
            help="Corporation ID to scope the backfill to. Defaults to SubsidyConfig.corporation_id.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Only include contracts issued in the last N days.",
        )
        parser.add_argument(
            "--date-from",
            type=str,
            default="",
            help="Inclusive lower bound on date_issued. Accepts YYYY-MM-DD or ISO datetime.",
        )
        parser.add_argument(
            "--date-to",
            type=str,
            default="",
            help="Inclusive upper bound on date_issued. Accepts YYYY-MM-DD or ISO datetime.",
        )
        parser.add_argument(
            "--status",
            action="append",
            default=[],
            help="Contract status to include. Repeat flag or pass comma-separated values.",
        )
        parser.add_argument(
            "--contract-id-start",
            type=int,
            default=None,
            help="Inclusive lower bound on CorporateContract.contract_id.",
        )
        parser.add_argument(
            "--contract-id-end",
            type=int,
            default=None,
            help="Inclusive upper bound on CorporateContract.contract_id.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=250,
            help="Number of contracts to process per matcher batch.",
        )
        parser.add_argument(
            "--only-missing-results",
            action="store_true",
            help="Skip contracts that already have DoctrineMatchResult rows.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Evaluate matches and report counts without persisting DoctrineMatchResult.",
        )

    def handle(self, *args, **options):
        corporation_id = options.get("corporation_id") or SubsidyConfig.active().corporation_id
        chunk_size = int(options.get("chunk_size") or 250)
        dry_run = bool(options.get("dry_run"))
        only_missing_results = bool(options.get("only_missing_results"))

        if chunk_size <= 0:
            raise CommandError("--chunk-size must be greater than 0.")

        days = options.get("days")
        date_from_raw = (options.get("date_from") or "").strip()
        date_to_raw = (options.get("date_to") or "").strip()
        if days is not None and (date_from_raw or date_to_raw):
            raise CommandError("Use either --days or --date-from/--date-to, not both.")

        date_from = self._parse_lower_bound(date_from_raw) if date_from_raw else None
        date_to = self._parse_upper_bound(date_to_raw) if date_to_raw else None
        if days is not None:
            if int(days) < 0:
                raise CommandError("--days must be 0 or greater.")
            date_from = timezone.now() - timedelta(days=int(days))
            date_to = timezone.now()

        statuses = self._normalize_statuses(options.get("status") or [])
        contract_id_start = options.get("contract_id_start")
        contract_id_end = options.get("contract_id_end")
        if contract_id_start and contract_id_end and int(contract_id_start) > int(contract_id_end):
            raise CommandError("--contract-id-start cannot be greater than --contract-id-end.")

        cfg = SubsidyConfig.active()
        qs = CorporateContract.objects.filter(corporation_id=corporation_id).order_by("id")
        qs = apply_contract_exclusions(qs, cfg)
        if date_from is not None:
            qs = qs.filter(date_issued__gte=date_from)
        if date_to is not None:
            qs = qs.filter(date_issued__lte=date_to)
        if statuses:
            status_q = Q()
            for status in statuses:
                status_q |= Q(status__iexact=status)
            qs = qs.filter(status_q)
        if contract_id_start is not None:
            qs = qs.filter(contract_id__gte=int(contract_id_start))
        if contract_id_end is not None:
            qs = qs.filter(contract_id__lte=int(contract_id_end))
        if only_missing_results:
            qs = qs.filter(doctrine_match__isnull=True)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No contracts matched the selected filters."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("Backfilling doctrine match results..."))
        self.stdout.write(
            f"corporation_id={corporation_id} total={total} chunk_size={chunk_size} dry_run={dry_run} "
            f"only_missing_results={only_missing_results}"
        )
        if date_from is not None or date_to is not None:
            self.stdout.write(
                f"date_issued range: {date_from.isoformat() if date_from else '*'} -> "
                f"{date_to.isoformat() if date_to else '*'}"
            )
        if statuses:
            self.stdout.write(f"statuses: {', '.join(statuses)}")
        if contract_id_start is not None or contract_id_end is not None:
            self.stdout.write(
                f"contract_id range: {contract_id_start if contract_id_start is not None else '*'} -> "
                f"{contract_id_end if contract_id_end is not None else '*'}"
            )

        processed = 0
        status_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()

        batch: list[int] = []
        for contract_pk in qs.values_list("id", flat=True).iterator(chunk_size=chunk_size):
            batch.append(int(contract_pk))
            if len(batch) < chunk_size:
                continue
            processed += self._process_batch(
                batch=batch,
                dry_run=dry_run,
                status_counts=status_counts,
                source_counts=source_counts,
            )
            self.stdout.write(f"processed {processed}/{total}")
            batch = []

        if batch:
            processed += self._process_batch(
                batch=batch,
                dry_run=dry_run,
                status_counts=status_counts,
                source_counts=source_counts,
            )
            self.stdout.write(f"processed {processed}/{total}")

        suffix = " (dry-run)" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"Doctrine match backfill complete{suffix}."))
        self.stdout.write(
            "match_status counts: "
            + ", ".join(
                f"{key}={status_counts.get(key, 0)}"
                for key in ("matched", "needs_review", "rejected")
            )
        )
        self.stdout.write(
            "match_source counts: "
            + ", ".join(
                f"{key}={source_counts.get(key, 0)}"
                for key in ("auto", "learned_rule", "forced", "manual_accept")
            )
        )

    def _process_batch(
        self,
        *,
        batch: list[int],
        dry_run: bool,
        status_counts: Counter[str],
        source_counts: Counter[str],
    ) -> int:
        results = match_contracts(batch, persist=not dry_run)
        for result in results.values():
            status_counts[result.match_status] += 1
            source_counts[result.match_source] += 1
        return len(batch)

    def _normalize_statuses(self, raw_statuses: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in raw_statuses:
            parts = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
            normalized.extend(parts)
        return normalized

    def _parse_lower_bound(self, raw: str) -> datetime:
        dt = parse_datetime(raw)
        if dt is not None:
            return self._make_aware(dt)
        d = parse_date(raw)
        if d is not None:
            return timezone.make_aware(datetime.combine(d, time.min))
        raise CommandError(f"Invalid --date-from value: {raw}")

    def _parse_upper_bound(self, raw: str) -> datetime:
        dt = parse_datetime(raw)
        if dt is not None:
            return self._make_aware(dt)
        d = parse_date(raw)
        if d is not None:
            return timezone.make_aware(datetime.combine(d, time.max))
        raise CommandError(f"Invalid --date-to value: {raw}")

    def _make_aware(self, value: datetime) -> datetime:
        if timezone.is_aware(value):
            return value
        return timezone.make_aware(value)
