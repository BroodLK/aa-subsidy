"""App Settings"""
from django.conf import settings

SUBSIDY_JANICE_API_KEY = getattr(settings, "SUBSIDY_JANICE_API_KEY", None)
