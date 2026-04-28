import json

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

class DoctrineSystem(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, help_text="If inactive, it won't be shown in the summary page or admin by default")

    def __str__(self) -> str:
        return self.name

    class Meta:
        verbose_name = "Doctrine System"
        verbose_name_plural = "Doctrine Systems"

class DoctrineLocation(models.Model):
    system = models.ForeignKey(DoctrineSystem, on_delete=models.CASCADE, related_name="locations")
    location = models.ForeignKey("eveuniverse.EveEntity", on_delete=models.CASCADE, related_name="+", help_text="Station, Structure or System")

    def __str__(self) -> str:
        return f"{self.system.name}: {self.location.name}"

    class Meta:
        verbose_name = "Doctrine Location"
        verbose_name_plural = "Doctrine Locations"
        unique_together = ("system", "location")

class FittingRequest(models.Model):
    system = models.ForeignKey(DoctrineSystem, on_delete=models.CASCADE, related_name="fitting_requests", null=True)
    fitting = models.ForeignKey(
        "fittings.Fitting", on_delete=models.CASCADE, related_name="subsidy_requests", db_index=True
    )
    requested = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Fitting Request"
        verbose_name_plural = "Fitting Requests"
        unique_together = ("system", "fitting")

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


class DoctrineMatchProfile(models.Model):
    fitting = models.OneToOneField(
        "fittings.Fitting",
        on_delete=models.CASCADE,
        related_name="match_profile",
    )
    enabled = models.BooleanField(default=True)
    auto_match_threshold = models.DecimalField(max_digits=5, decimal_places=2, default=95)
    review_threshold = models.DecimalField(max_digits=5, decimal_places=2, default=80)
    allow_extra_items = models.BooleanField(default=True)
    allow_meta_variants = models.BooleanField(default=False)
    allow_faction_variants = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Doctrine Match Profile"
        verbose_name_plural = "Doctrine Match Profiles"

    def __str__(self) -> str:
        return f"Match Profile: {self.fitting_id}"


class DoctrineItemRule(models.Model):
    RULE_REQUIRED = "required"
    RULE_OPTIONAL = "optional"
    RULE_CARGO = "cargo"
    RULE_IGNORE = "ignore"
    RULE_KIND = (
        (RULE_REQUIRED, "Required"),
        (RULE_OPTIONAL, "Optional"),
        (RULE_CARGO, "Cargo"),
        (RULE_IGNORE, "Ignore"),
    )

    QTY_EXACT = "exact"
    QTY_MINIMUM = "minimum"
    QTY_RANGE = "range"
    QTY_MODE = (
        (QTY_EXACT, "Exact"),
        (QTY_MINIMUM, "Minimum"),
        (QTY_RANGE, "Range"),
    )

    profile = models.ForeignKey(
        DoctrineMatchProfile,
        on_delete=models.CASCADE,
        related_name="item_rules",
    )
    eve_type = models.ForeignKey(
        "eveuniverse.EveType",
        on_delete=models.CASCADE,
        related_name="+",
    )
    rule_kind = models.CharField(max_length=16, choices=RULE_KIND, default=RULE_REQUIRED)
    quantity_mode = models.CharField(max_length=16, choices=QTY_MODE, default=QTY_EXACT)
    expected_quantity = models.IntegerField(default=1)
    min_quantity = models.IntegerField(default=0)
    max_quantity = models.IntegerField(default=0)
    category = models.CharField(max_length=32, default="module")
    slot_label = models.CharField(max_length=64, blank=True, default="")
    sort_order = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Doctrine Item Rule"
        verbose_name_plural = "Doctrine Item Rules"
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return f"{self.profile_id}:{self.eve_type_id}:{self.rule_kind}"


class DoctrineSubstitutionRule(models.Model):
    RULE_SPECIFIC = "specific"
    RULE_META_FAMILY = "meta_family"
    RULE_MARKET_GROUP = "market_group"
    RULE_GROUP = "group"
    RULE_TYPE = (
        (RULE_SPECIFIC, "Specific Type Substitute"),
        (RULE_META_FAMILY, "Same Meta Family"),
        (RULE_MARKET_GROUP, "Same Market Group"),
        (RULE_GROUP, "Same Group"),
    )

    profile = models.ForeignKey(
        DoctrineMatchProfile,
        on_delete=models.CASCADE,
        related_name="substitutions",
    )
    expected_type = models.ForeignKey(
        "eveuniverse.EveType",
        on_delete=models.CASCADE,
        related_name="+",
    )
    allowed_type = models.ForeignKey(
        "eveuniverse.EveType",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    rule_type = models.CharField(max_length=20, choices=RULE_TYPE, default=RULE_SPECIFIC)
    max_meta_level_delta = models.IntegerField(default=0)
    same_slot_only = models.BooleanField(default=True)
    same_group_only = models.BooleanField(default=True)
    penalty_points = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Doctrine Substitution Rule"
        verbose_name_plural = "Doctrine Substitution Rules"
        ordering = ("expected_type_id", "id")

    def __str__(self) -> str:
        return f"{self.profile_id}:{self.expected_type_id}->{self.allowed_type_id or self.rule_type}"


class DoctrineQuantityTolerance(models.Model):
    MODE_ABSOLUTE = "absolute"
    MODE_PERCENT = "percent"
    MODE_MISSING_ONLY = "missing_only"
    MODE_EXTRA_ONLY = "extra_only"
    MODE_CHOICES = (
        (MODE_ABSOLUTE, "Absolute"),
        (MODE_PERCENT, "Percent"),
        (MODE_MISSING_ONLY, "Missing Only"),
        (MODE_EXTRA_ONLY, "Extra Only"),
    )

    profile = models.ForeignKey(
        DoctrineMatchProfile,
        on_delete=models.CASCADE,
        related_name="quantity_tolerances",
    )
    eve_type = models.ForeignKey(
        "eveuniverse.EveType",
        on_delete=models.CASCADE,
        related_name="+",
    )
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_ABSOLUTE)
    lower_bound = models.IntegerField(default=0)
    upper_bound = models.IntegerField(default=0)
    penalty_points = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Doctrine Quantity Tolerance"
        verbose_name_plural = "Doctrine Quantity Tolerances"
        ordering = ("eve_type_id", "id")

    def __str__(self) -> str:
        return f"{self.profile_id}:{self.eve_type_id}:{self.mode}"


class DoctrineMatchResult(models.Model):
    STATUS_MATCHED = "matched"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_MATCHED, "Matched"),
        (STATUS_NEEDS_REVIEW, "Needs Review"),
        (STATUS_REJECTED, "Rejected"),
    )

    SOURCE_AUTO = "auto"
    SOURCE_FORCED = "forced"
    SOURCE_LEARNED = "learned_rule"
    SOURCE_MANUAL_ACCEPT = "manual_accept"
    SOURCE_CHOICES = (
        (SOURCE_AUTO, "Auto"),
        (SOURCE_FORCED, "Forced"),
        (SOURCE_LEARNED, "Learned Rule"),
        (SOURCE_MANUAL_ACCEPT, "Manual Accept"),
    )

    contract = models.OneToOneField(
        "corptools.CorporateContract",
        on_delete=models.CASCADE,
        related_name="doctrine_match",
        db_index=True,
    )
    matched_fitting = models.ForeignKey(
        "fittings.Fitting",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="doctrine_match_results",
    )
    match_source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_AUTO)
    match_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEEDS_REVIEW)
    score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    hard_failures_json = models.TextField(default="[]", blank=True)
    warnings_json = models.TextField(default="[]", blank=True)
    evidence_json = models.TextField(default="{}", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Doctrine Match Result"
        verbose_name_plural = "Doctrine Match Results"
        indexes = [
            models.Index(fields=["match_source"], name="dmr_match_source_idx"),
            models.Index(fields=["match_status"], name="dmr_match_status_idx"),
            models.Index(fields=["updated_at"], name="dmr_updated_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}:{self.matched_fitting_id or 'none'}:{self.match_status}"

    @staticmethod
    def _loads(raw: str, default):
        try:
            return json.loads(raw or "")
        except (TypeError, ValueError):
            return default

    @property
    def hard_failures(self):
        return self._loads(self.hard_failures_json, [])

    @property
    def warnings(self):
        return self._loads(self.warnings_json, [])

    @property
    def evidence(self):
        return self._loads(self.evidence_json, {})


class DoctrineContractDecision(models.Model):
    DECISION_ACCEPT_ONCE = "accept_once"
    DECISION_REJECT_ONCE = "reject_once"
    DECISION_CREATE_RULE = "create_rule"
    DECISION_CREATE_VARIANT = "create_variant"
    DECISION = (
        (DECISION_ACCEPT_ONCE, "Accept Once"),
        (DECISION_REJECT_ONCE, "Reject Once"),
        (DECISION_CREATE_RULE, "Create Rule"),
        (DECISION_CREATE_VARIANT, "Create Variant"),
    )

    contract = models.ForeignKey(
        "corptools.CorporateContract",
        on_delete=models.CASCADE,
        related_name="match_decisions",
    )
    fitting = models.ForeignKey(
        "fittings.Fitting",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decision = models.CharField(max_length=20, choices=DECISION)
    summary = models.CharField(max_length=255, default="")
    details_json = models.TextField(default="{}", blank=True)
    created_by = models.ForeignKey(
        "auth.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Doctrine Contract Decision"
        verbose_name_plural = "Doctrine Contract Decisions"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["decision"], name="dcd_decision_idx"),
            models.Index(fields=["created_at"], name="dcd_created_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}:{self.decision}"

    @property
    def details(self):
        try:
            return json.loads(self.details_json or "{}")
        except (TypeError, ValueError):
            return {}

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
    corporation_id = models.IntegerField(default=1, help_text="The ID of the corporation whose contracts should be subsidized")

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
