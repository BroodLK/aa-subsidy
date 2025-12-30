from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
import json

class Command(BaseCommand):
    help = 'Setup periodic tasks for aa-subsidy'

    def handle(self, *args, **options):
        # 1. Sync fittings once a minute
        schedule_1min, _ = IntervalSchedule.objects.get_or_create(
            every=1,
            period=IntervalSchedule.MINUTES,
        )

        PeriodicTask.objects.update_or_create(
            name='AA Subsidy: Sync Fitting Requests',
            defaults={
                'interval': schedule_1min,
                'task': 'aasubsidy.tasks.sync_fitting_requests',
            }
        )
        self.stdout.write(self.style.SUCCESS('Scheduled sync_fitting_requests every minute'))

        # 2. Sync contracts once an hour
        schedule_1hour, _ = IntervalSchedule.objects.get_or_create(
            every=1,
            period=IntervalSchedule.HOURS,
        )

        PeriodicTask.objects.update_or_create(
            name='AA Subsidy: Import Corporate Contract Reviews',
            defaults={
                'interval': schedule_1hour,
                'task': 'aasubsidy.tasks.import_corporate_contract_reviews',
            }
        )
        self.stdout.write(self.style.SUCCESS('Scheduled import_corporate_contract_reviews every hour'))

        # 3. Refresh prices once a week (e.g., Sunday at 01:00)
        schedule_weekly, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='1',
            day_of_week='0',
            day_of_month='*',
            month_of_year='*',
        )

        PeriodicTask.objects.update_or_create(
            name='AA Subsidy: Refresh Subsidy Item Prices',
            defaults={
                'crontab': schedule_weekly,
                'task': 'aasubsidy.tasks.refresh_subsidy_item_prices',
            }
        )
        self.stdout.write(self.style.SUCCESS('Scheduled refresh_subsidy_item_prices weekly'))

        # 4. Seed types into subsidy once a week (e.g., Sunday at 00:00)
        schedule_seed, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='0',
            day_of_week='0',
            day_of_month='*',
            month_of_year='*',
        )

        PeriodicTask.objects.update_or_create(
            name='AA Subsidy: Seed All Types Into Subsidy',
            defaults={
                'crontab': schedule_seed,
                'task': 'aasubsidy.tasks.seed_all_types_into_subsidy',
            }
        )
        self.stdout.write(self.style.SUCCESS('Scheduled seed_all_types_into_subsidy weekly'))
