import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testauth.settings.local")
django.setup()

from corptools.models import CorporateContract
from aasubsidy.models import CorporateContractSubsidy, DoctrineMatchResult
from aasubsidy.contracts.matching import match_contract

# Find a contract
cc = CorporateContract.objects.first()
if not cc:
    print("No contracts found")
    exit()

print(f"Testing with contract PK={cc.pk}, ESI ID={cc.contract_id}")

# Force a fit (assuming fitting with ID 1 exists, otherwise find one)
from fittings.models import Fitting
fit = Fitting.objects.first()
if not fit:
    print("No fittings found")
    exit()

print(f"Forcing to fit: {fit.name} (ID={fit.pk})")
meta, _ = CorporateContractSubsidy.objects.get_or_create(contract=cc)
meta.forced_fitting = fit
meta.save()

# Run matching
result = match_contract(cc.pk, persist=True)
print(f"Match source after forcing: {result.match_source}")
print(f"Matched fitting ID: {result.matched_fitting_id}")

# Clear choice
print("Clearing choice...")
meta.forced_fitting = None
meta.save()

# Run matching again
result = match_contract(cc.pk, persist=True)
print(f"Match source after clearing: {result.match_source}")
print(f"Matched fitting ID: {result.matched_fitting_id}")

if result.match_source == "forced":
    print("BUG REPRODUCED: Still forced!")
else:
    print("Result is not forced. Source:", result.match_source)
