#!/usr/bin/env python
import os
import django

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'testauth.settings.local')
django.setup()

# Now import and run the task
from aasubsidy.tasks import sync_corporate_contracts_from_esi

print("Starting ESI contract pull with items...")
result = sync_corporate_contracts_from_esi(force_refresh=False)
print("\nResult:")
print(result)
