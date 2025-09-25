from django.db.models import Func

class Ceil(Func):
    function = "CEIL"

class Round(Func):
    function = "ROUND"