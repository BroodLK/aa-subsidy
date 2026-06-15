"""App Settings"""
from django.conf import settings

SUBSIDY_JANICE_API_KEY = getattr(settings, "SUBSIDY_JANICE_API_KEY", None)
SUBSIDY_ESI_COMPATIBILITY_DATE = getattr(
    settings,
    "SUBSIDY_ESI_COMPATIBILITY_DATE",
    "2025-08-26",
)
