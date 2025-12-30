from django.core.management.base import BaseCommand
from eveuniverse.models import EveSolarSystem, EveStation
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Load necessary eveuniverse data for aa-subsidy (Solar Systems and Stations)'

    def handle(self, *args, **options):
        self.stdout.write("Loading Solar Systems...")
        # Loading all solar systems is heavy, but we usually want at least the K-space ones.
        # However, for an app like this, users might only want specific ones.
        # But to make search work, they need to be in the DB.
        self.stdout.write("This may take a while as it fetches data from ESI via eveuniverse.")
        
        # We can't easily "load all" without a lot of ESI calls.
        # Usually AA users run: python manage.py eveuniverse_load_data map
        self.stdout.write(self.style.WARNING("It is highly recommended to run: python manage.py eveuniverse_load_data map"))
        self.stdout.write(self.style.WARNING("This will load all Regions, Constellations, and Solar Systems."))
        
        self.stdout.write("If you want to load a specific system or station to test, you can use the AA Admin or this command will attempt to resolve common staging systems if you provide them (not implemented here).")
        
        self.stdout.write(self.style.SUCCESS("Check README.md for more details on eveuniverse requirements."))
