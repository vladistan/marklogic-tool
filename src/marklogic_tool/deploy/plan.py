"""The plan document. Contract `marklogic-tool/deploy-plan/1`.

This model is mutable on purpose. The caller builds it before reconcile and mutates it in
place, so a `finally` emits it on every exit path. Every collection defaults empty.
"""

# cspell:ignore unforceable

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLAN_SCHEMA: str = "marklogic-tool/deploy-plan/1"
"""Self-identifying contract name.

Carried in-band so an agent consumer can tell which version it received without
out-of-band knowledge — the same gap that was closed for the verification schema.
"""


class PlanStatus(StrEnum):
    """The complete status vocabulary. There is no fifth value.

    `blocked` means "fix the declaration or the server" and is the only status that
    carries a `blocked_reason`.
    """

    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"


class PropertyChange(BaseModel):
    """One property-level change, audited in Manage-native terms.

    `property` is the single place a Manage-native name is allowed to surface to the
    operator; everything else in the tool speaks the user vocabulary.
    """

    model_config = ConfigDict(extra="forbid")

    property: str
    observed: Any | None = None
    desired: Any | None = None
    redacted: bool = False

    @model_validator(mode="after")
    def _redacted_carries_no_observed_value(self) -> "PropertyChange":
        # Passwords are unobservable and must never round-trip into a plan file,
        # a log or a terminal. Enforced rather than documented: a plan document is
        # exactly the artefact an operator pastes into a ticket.
        if self.redacted and self.observed is not None:
            msg = (
                f"property {self.property!r} is marked redacted but carries an "
                f"observed value; a redacted change must record no observed value"
            )
            raise ValueError(msg)
        return self

    @classmethod
    def redacted_change(
        cls, property_name: str, desired: Any = None
    ) -> "PropertyChange":
        """Build a change whose observed value is withheld by construction."""
        return cls(property=property_name, desired=desired, redacted=True)


class ObjectPlan(BaseModel):
    """The planned disposition of one declared object."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    status: PlanStatus
    applied: bool = False
    changes: list[PropertyChange] = Field(default_factory=list)
    suppressed_changes: list[PropertyChange] = Field(default_factory=list)
    blocked_reason: str | None = None
    force_required: bool = False
    forced: bool = False
    """True when this object applied ONLY because `--force` was given.

    Wire-visible on purpose. An object written under duress must stay
    distinguishable from an ordinary update in the artifact, or a forced override is
    laundered into a routine change and the audit record loses the one fact that
    mattered. `blocked_reason` is kept alongside it, so the plan still says WHAT was
    overridden.
    """

    blocked_by: dict[str, str] = Field(default_factory=dict, exclude=True)
    """Which declared property earned which blocked reason. Internal, not on the wire.

    `blocked_reason` is a joined string for humans; attribution is needed by
    `apply_suppressions`, because a block earned solely by a suppressed property must
    be cleared while a block earned by anything else must survive. Recovering
    that by matching property names inside the message would be a text-scan over our
    own output — the shape that has bitten this unit repeatedly.
    """

    unforceable: list[str] = Field(default_factory=list, exclude=True)
    """Blocked properties that `--force` must never reach. Internal."""
    depends_on_pending: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PlanSummary(BaseModel):
    """Counts by status. Reporting only — never a gate."""

    model_config = ConfigDict(extra="forbid")

    create: int = 0
    update: int = 0
    unchanged: int = 0
    blocked: int = 0
    total: int = 0


class DeployPlan(BaseModel):
    """The plan document. Mutable and incrementally populated."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plan_schema: str = Field(default=PLAN_SCHEMA, alias="schema")
    mode: Literal["plan", "apply"]
    target: list[str] = Field(default_factory=list)
    # Provenance: MarkLogic 11 and 12 expose different property sets, so a diff that
    # looks spurious is attributable to the server version rather than to the tool.
    # Left None until something observes it; the plan never invents one.
    ml_version: str | None = None
    objects: list[ObjectPlan] = Field(default_factory=list)
    summary: PlanSummary = Field(default_factory=PlanSummary)
    warnings: list[str] = Field(default_factory=list)
    exit_code: int | None = None

    @classmethod
    def new(cls, *, mode: Literal["plan", "apply"], target: list[str]) -> "DeployPlan":
        """Construct an empty plan before `reconcile` is entered."""
        return cls(mode=mode, target=list(target))

    def add_object(self, obj: ObjectPlan) -> ObjectPlan:
        """Append one classified object and keep the summary in step.

        Returns the object so the caller can keep mutating it (marking `applied`,
        appending notes) as the apply path progresses.
        """
        self.objects.append(obj)
        self.refresh_summary()
        return obj

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def refresh_summary(self) -> None:
        """Recount from `objects`. Cheap, and safe to call at any moment."""
        counts = dict.fromkeys(PlanStatus, 0)
        for obj in self.objects:
            counts[obj.status] += 1
        self.summary = PlanSummary(
            create=counts[PlanStatus.CREATE],
            update=counts[PlanStatus.UPDATE],
            unchanged=counts[PlanStatus.UNCHANGED],
            blocked=counts[PlanStatus.BLOCKED],
            total=len(self.objects),
        )

    def to_wire(self) -> dict[str, Any]:
        """Serialise to the wire shape, with `schema` as the first key."""
        return self.model_dump(by_alias=True, mode="json")
