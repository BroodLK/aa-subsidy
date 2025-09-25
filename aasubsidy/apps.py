"""App Configuration"""

# Django
from django.apps import AppConfig

# AA Example App
from aasubsidy import __version__


class ExampleConfig(AppConfig):
    """App Config"""

    name = "aasubsidy"
    label = "aasubsidy"
    verbose_name = f"Example App v{__version__}"
