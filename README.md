# aa-subsidy
Subsidize contracts that match doctrine fittings to keep prices at Jita or better


# How to use?
You probably shouldn't

### Setup
After installing, you should run the following command to setup the periodic tasks:
```bash
python manage.py setup_aasubsidy_tasks
```
This will schedule:
- Price refresh (weekly)
- Contract sync (hourly)
- Fitting sync (minutely)