"""App Configuration"""

# Django
from django.apps import AppConfig

# AA Subsidy App
from aasubsidy import __version__


class AasubsidyConfig(AppConfig):
    """App Config"""

    name = "aasubsidy"
    label = "aasubsidy"
    verbose_name = f"AA Subsidy v{__version__}"
