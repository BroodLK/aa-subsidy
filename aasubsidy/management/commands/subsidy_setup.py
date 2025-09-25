from celery.schedules import crontab
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from django.core.management.base import BaseCommand

from allianceauth.crontab.utils import offset_cron

class Command(BaseCommand):
    help = 'Bootstrap the Subsidy Module tasks'

    def handle(self, *args, **options):
        self.stdout.write("Configuring Subsidy Tasks!")

        # Weekly at 03:00 UTC on Sunday (Celery uses 0-6 for Mon-Sun)
        weekly_price = CrontabSchedule.from_schedule(
            offset_cron(crontab(day_of_week='0', hour='3', minute='0'))
        )
        price_cron, _ = CrontabSchedule.objects.get_or_create(
            minute=weekly_price.minute,
            hour=weekly_price.hour,
            day_of_month=weekly_price.day_of_month,
            month_of_year=weekly_price.month_of_year,
            day_of_week=weekly_price.day_of_week,
            timezone=weekly_price.timezone,
        )

        # Every 30 minutes offset at :20 and :50 for contract review sync
        review_schedule = CrontabSchedule.from_schedule(
            offset_cron(crontab(minute='20,50'))
        )
        review_cron, _ = CrontabSchedule.objects.get_or_create(
            minute=review_schedule.minute,
            hour=review_schedule.hour,
            day_of_month=review_schedule.day_of_month,
            month_of_year=review_schedule.month_of_year,
            day_of_week=review_schedule.day_of_week,
            timezone=review_schedule.timezone,
        )

        # Every minute
        fitting_schedule = CrontabSchedule.from_schedule(
            offset_cron(crontab(minute='*', hour='*'))
        )
        fitting_cron, _ = CrontabSchedule.objects.get_or_create(
            minute=fitting_schedule.minute,
            hour=fitting_schedule.hour,
            day_of_month=fitting_schedule.day_of_month,
            month_of_year=fitting_schedule.month_of_year,
            day_of_week=fitting_schedule.day_of_week,
            timezone=fitting_schedule.timezone,
        )

        PeriodicTask.objects.update_or_create(
            name='Subsidy: Refresh Item Prices',
            defaults={
                'task': 'aasubsidy.tasks.refresh_subsidy_item_prices',
                'crontab': price_cron,
                'enabled': True
            }
        )

        PeriodicTask.objects.update_or_create(
            name='Subsidy: Import Corporate Contract Reviews',
            defaults={
                'task': 'aasubsidy.tasks.import_corporate_contract_reviews',
                'crontab': review_cron,
                'enabled': True,
            }
        )

        PeriodicTask.objects.update_or_create(
            name='Subsidy: Sync Fitting Requests',
            defaults={
                'task': 'aasubsidy.tasks.sync_fitting_requests',
                'crontab': fitting_cron,
                'enabled': True
            }
        )

        self.stdout.write("Configured Subsidy Tasks!")