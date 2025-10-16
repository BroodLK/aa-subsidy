from django.db import models


class General(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access this app"),
            ("review_subsidy", "Can Review/Approve/Deny Subsidies"),
            ("subsidy_admin", "Can adjust subsidy settings"),
        )

class FittingRequest(models.Model):
    fitting = models.OneToOneField(
        "fittings.Fitting", on_delete=models.CASCADE, related_name="subsidy_request", db_index=True
    )
    requested = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Fitting Request"
        verbose_name_plural = "Fitting Requests"

class SubsidyItemPrice(models.Model):
    eve_type = models.OneToOneField(
        "eveuniverse.EveType", on_delete=models.CASCADE, related_name="subsidy_price", db_index=True
    )
    sell = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    buy = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Subsidy Item Price"
        verbose_name_plural = "Subsidy Item Prices"


class CorporateContractSubsidy(models.Model):
    contract = models.OneToOneField(
        "corptools.CorporateContract",
        on_delete=models.CASCADE,
        related_name="aasubsidy_meta",
        db_index=True,
    )
    review_status = models.SmallIntegerField(
        default=0,
        help_text="1=Approved, -1=Rejected, 0=Pending",
    )
    subsidy_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    reason = models.TextField(blank=True, default="")
    paid = models.BooleanField(default=False)
    exempt = models.BooleanField(default=False)
    forced_fitting = models.ForeignKey("fittings.Fitting", null=True, blank=True, on_delete=models.SET_NULL, related_name="forced_contracts", db_index=True)

    class Meta:
        verbose_name = "Corporate Contract Subsidy"
        verbose_name_plural = "Corporate Contract Subsidies"
        constraints = [
            models.UniqueConstraint(fields=["contract"], name="uniq_subsidy_per_contract"),
        ]
        indexes = [
            models.Index(fields=["contract"], name="uniqs_subsidy_per_contract"),
            models.Index(fields=["review_status"], name="ccs_review_status_idxs"),
            models.Index(fields=["paid"], name="ccs_paid_idxs"),
            models.Index(fields=["exempt"], name="ccs_exempt_idxs"),
            models.Index(fields=["subsidy_amount"], name="ccs_subsidy_amount_idxs"),
            models.Index(fields=["reason"], name="ccs_reason_idxs"),
        ]

    @property
    def review_status_label(self) -> str:
        return {1: "Approved", -1: "Rejected"}.get(self.review_status, "Pending")

    @property
    def status_num(self) -> int:
        if self.review_status == 1:
            return 1
        if self.review_status == -1:
            return -1
        return 0

class SubsidyConfig(models.Model):
    PRICE_BASIS_CHOICES = (
        ("sell", "Jita Sell"),
        ("buy", "Jita Buy"),
    )
    price_basis = models.CharField(max_length=4, choices=PRICE_BASIS_CHOICES, default="sell")
    pct_over_basis = models.DecimalField(max_digits=6, decimal_places=4, default=0.10, help_text="e.g. 0.10 for 10%")
    cost_per_m3 = models.DecimalField(max_digits=20, decimal_places=4, default=250)
    rounding_increment = models.IntegerField(default=250000, help_text="ISK rounding increment")
    deleted_check = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Subsidy Configuration"
        verbose_name_plural = "Subsidy Configuration"

    def __str__(self) -> str:
        return f"SubsidyConfig(basis={self.price_basis}, pct={self.pct_over_basis}, m3={self.cost_per_m3})"

    @classmethod
    def active(cls) -> "SubsidyConfig":
        obj = cls.objects.first()
        if obj:
            return obj
        return cls.objects.create()


class UserTablePreference(models.Model):
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="aasubsidy_table_prefs", db_index=True)
    table_key = models.CharField(max_length=100, default="contracts", db_index=True)
    sort_idx = models.IntegerField(default=0)
    sort_dir = models.CharField(max_length=4, default="desc")
    filters_json = models.TextField(blank=True, default="{}")

    class Meta:
        unique_together = ("user", "table_key")
        verbose_name = "User Table Preference"
        verbose_name_plural = "User Table Preferences"

    def __str__(self) -> str:
        return f"{self.user_id}:{self.table_key}"


class FittingClaim(models.Model):
    fitting = models.ForeignKey("fittings.Fitting", on_delete=models.CASCADE, related_name="subsidy_claims", db_index=True)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="subsidy_fitting_claims", db_index=True)
    quantity = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Fitting Claim"
        verbose_name_plural = "Fitting Claims"
        unique_together = ("fitting", "user")

    def __str__(self) -> str:
        return f"{self.fitting_id}:{self.user_id}={self.quantity}"
