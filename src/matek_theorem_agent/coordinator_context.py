"""Deterministic, evidence-preserving context construction for research coordination."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def serialize_coordinator_payload(payload: Mapping[str, object]) -> str:
    """Return the exact canonical JSON sent as the coordinator's stage input."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class CoordinatorArtifactReference(BaseModel):
    """Authenticated address for evidence retained outside the working context."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    kind: Literal["worker_report", "graph_node", "candidate", "audit", "event"]
    relative_path: str
    sha256: str
    assignment_id: str | None = None
    graph_node_id: str | None = None
    graph_revision: str | None = None


class CoordinatorEvidenceItem(BaseModel):
    """One complete artifact plus its deterministic structured summary."""

    model_config = ConfigDict(extra="forbid")

    reference: CoordinatorArtifactReference
    summary: dict[str, object]
    full_content: dict[str, object]
    priority: int = Field(ge=0)
    inclusion_reason: str


class CoordinatorContextManifest(BaseModel):
    """Reproducible account of one exact provider working set."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 2
    decision_id: int = Field(ge=1)
    after_event_sequence: int = Field(ge=0)
    mode: Literal["normal", "compact", "indexed"]
    configured_character_limit: int = Field(gt=0)
    effective_character_limit: int = Field(gt=0)
    serialized_payload_characters: int = Field(ge=0)
    serialized_provider_input_characters: int = Field(ge=0)
    estimated_input_tokens: int = Field(ge=0)
    payload_sha256: str
    included_full_artifacts: list[dict[str, str]] = Field(default_factory=list)
    omitted_artifacts: list[CoordinatorArtifactReference] = Field(default_factory=list)
    aggregated_event_groups: list[dict[str, object]] = Field(default_factory=list)
    requested_artifact_ids: list[str] = Field(default_factory=list)
    requested_graph_node_ids: list[str] = Field(default_factory=list)
    omitted_state_sections: list[dict[str, object]] = Field(default_factory=list)


@dataclass(frozen=True)
class CoordinatorContextBuild:
    payload: dict[str, object]
    serialized_input: str
    manifest: CoordinatorContextManifest


class CoordinatorContextBudgetExhausted(RuntimeError):
    """The irreducible prompt/claim envelope cannot fit the transport budget."""

    def __init__(self, *, limit: int, required: int) -> None:
        self.limit = limit
        self.required = required
        super().__init__(
            "CONTEXT_BUDGET_EXHAUSTED: the irreducible coordinator prompt/claim envelope requires "
            f"{required} serialized provider characters but the effective limit is {limit}."
        )


def _aggregate_repetitive_events(
    events: Iterable[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    repetitive_kinds = {
        "coordinator_input_too_large",
        "graph_mutation_rejected",
        "worker_execution_failed",
        "worker_repair_unavailable",
    }
    ordinary: list[dict[str, object]] = []
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for event in events:
        kind = event.get("kind")
        if not isinstance(kind, str) or kind not in repetitive_kinds:
            ordinary.append(event)
            continue
        detail = event.get("detail", [])
        normalized_detail = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        groups[(kind, normalized_detail)].append(event)

    aggregated: list[dict[str, object]] = []
    group_evidence: list[dict[str, object]] = []
    for (kind, _), members in sorted(groups.items()):
        if len(members) == 1:
            ordinary.append(members[0])
            continue
        assignment_ids = sorted(
            {
                assignment_id
                for member in members
                if isinstance((assignment_id := member.get("assignment_id")), str)
            }
        )
        issue_paths = sorted(
            {path for member in members if isinstance((path := member.get("artifact")), str)}
        )
        sequences = sorted(
            sequence
            for member in members
            if isinstance((sequence := member.get("sequence")), int)
            and not isinstance(sequence, bool)
        )
        group = {
            "schema_version": 1,
            "kind": f"{kind}_aggregate",
            "count": len(members),
            "first_sequence": sequences[0] if sequences else None,
            "last_sequence": sequences[-1] if sequences else None,
            "affected_assignment_ids": assignment_ids,
            "issue_paths": issue_paths,
            "detail": members[0].get("detail", []),
        }
        aggregated.append(group)
        group_evidence.append(group)
    combined = [*ordinary, *aggregated]

    def event_sort_key(event: dict[str, object]) -> tuple[int, str]:
        raw_sequence = event.get("sequence", event.get("first_sequence", 0))
        sequence = (
            raw_sequence
            if isinstance(raw_sequence, int) and not isinstance(raw_sequence, bool)
            else 0
        )
        return sequence, str(event.get("kind", ""))

    combined.sort(key=event_sort_key)
    return combined, group_evidence


def _compact_event(event: Mapping[str, object]) -> dict[str, object]:
    """Preserve event identity and obligations without carrying unbounded prose."""

    result = {
        key: event[key]
        for key in (
            "schema_version",
            "sequence",
            "first_sequence",
            "last_sequence",
            "kind",
            "count",
            "assignment_id",
            "decision_id",
            "response_id",
            "artifact",
            "artifact_sha256",
            "related_artifacts",
            "affected_assignment_ids",
            "issue_paths",
        )
        if key in event
    }
    raw_detail = event.get("detail", [])
    detail = raw_detail if isinstance(raw_detail, list) else [raw_detail]
    result["detail_summary"] = [
        normalized if len(normalized) <= 320 else normalized[:319].rstrip() + "…"
        for item in detail[:8]
        if (normalized := " ".join(str(item).split()))
    ]
    if len(detail) > 8:
        result["omitted_detail_items"] = len(detail) - 8
    return result


class CoordinatorContextBuilder:
    """Build a complete small context or a prioritized compact working set."""

    def __init__(
        self,
        *,
        configured_character_limit: int,
        effective_character_limit: int | None = None,
        provider_input_characters: Callable[[str], int] | None = None,
    ) -> None:
        if configured_character_limit <= 0:
            raise ValueError("coordinator context character limit must be positive")
        self.configured_character_limit = configured_character_limit
        self.effective_character_limit = effective_character_limit or configured_character_limit
        if self.effective_character_limit <= 0:
            raise ValueError("effective coordinator context limit must be positive")
        if self.effective_character_limit > configured_character_limit:
            raise ValueError("effective coordinator limit cannot exceed its configured limit")
        self._provider_input_characters = provider_input_characters or len

    def _measure(self, payload: Mapping[str, object]) -> tuple[str, int]:
        serialized = serialize_coordinator_payload(payload)
        return serialized, self._provider_input_characters(serialized)

    def build(
        self,
        *,
        decision_id: int,
        after_event_sequence: int,
        normal_payload: dict[str, object],
        compact_base: dict[str, object],
        indexed_base: dict[str, object] | None = None,
        events: list[dict[str, object]],
        assignment_table: list[dict[str, object]],
        report_evidence: list[CoordinatorEvidenceItem],
        graph_memory: dict[str, object] | None,
        graph_evidence: list[CoordinatorEvidenceItem] | None = None,
        requested_artifact_ids: list[str] | None = None,
        requested_graph_node_ids: list[str] | None = None,
        force_compact: bool = False,
    ) -> CoordinatorContextBuild:
        requested_artifacts = list(dict.fromkeys(requested_artifact_ids or []))
        requested_graph_nodes = list(dict.fromkeys(requested_graph_node_ids or []))
        all_evidence = [*report_evidence, *(graph_evidence or [])]
        normal_serialized, normal_characters = self._measure(normal_payload)
        if (
            not force_compact
            and not graph_evidence
            and normal_characters <= self.effective_character_limit
        ):
            normal_included = [
                {
                    "artifact_id": item.reference.artifact_id,
                    "reason": "normal context includes complete current evidence",
                }
                for item in all_evidence
            ]
            manifest = self._manifest(
                decision_id=decision_id,
                after_event_sequence=after_event_sequence,
                mode="normal",
                payload=normal_payload,
                serialized=normal_serialized,
                provider_characters=normal_characters,
                included=normal_included,
                omitted=[],
                aggregated=[],
                requested_artifacts=requested_artifacts,
                requested_graph_nodes=requested_graph_nodes,
            )
            return CoordinatorContextBuild(normal_payload, normal_serialized, manifest)

        compact_events, aggregated = _aggregate_repetitive_events(events)
        catalog = [item.reference.model_dump(mode="json") for item in all_evidence]
        payload = {
            **compact_base,
            "context_mode": "compact",
            "context_contract": {
                "raw_evidence_is_authoritative": True,
                "references_are_bound_to_frozen_sha256": True,
                "request_omitted_evidence_with": [
                    "requested_artifact_ids",
                    "requested_graph_node_ids",
                ],
            },
            "assignment_lifecycle": assignment_table,
            "unacknowledged_events": compact_events,
            "report_summaries": [],
            "visible_worker_reports": [],
            "requested_artifacts": [],
            "requested_graph_nodes": [],
            "artifact_catalog": catalog,
        }
        if graph_memory is not None:
            payload["knowledge_graph_memory"] = graph_memory

        serialized, provider_characters = self._measure(payload)
        if provider_characters > self.effective_character_limit:
            if indexed_base is None:
                raise CoordinatorContextBudgetExhausted(
                    limit=self.effective_character_limit,
                    required=provider_characters,
                )
            return self._build_indexed(
                decision_id=decision_id,
                after_event_sequence=after_event_sequence,
                indexed_base=indexed_base,
                events=compact_events,
                assignment_table=assignment_table,
                evidence=all_evidence,
                aggregated=aggregated,
                requested_artifacts=requested_artifacts,
                requested_graph_nodes=requested_graph_nodes,
            )

        included: list[dict[str, str]] = []
        included_ids: set[str] = set()
        ordered = sorted(
            all_evidence,
            key=lambda item: (item.priority, item.reference.artifact_id),
        )

        # Structured summaries are independently useful and much smaller than proofs.
        for item in ordered:
            key = (
                "graph_node_summaries"
                if item.reference.kind == "graph_node"
                else "report_summaries"
            )
            current = payload.setdefault(key, [])
            assert isinstance(current, list)
            current.append(item.summary)
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > self.effective_character_limit:
                current.pop()
            else:
                serialized, provider_characters = candidate_serialized, candidate_characters

        for item in ordered:
            reference = item.reference
            if reference.kind == "worker_report" and item.priority >= 10:
                # Older progress remains indexed and summarized. Complete prose is
                # reserved for new, candidate-producing, redirected, or requested work.
                continue
            requested = reference.artifact_id in requested_artifacts or (
                reference.graph_node_id is not None
                and reference.graph_node_id in requested_graph_nodes
            )
            if reference.kind == "graph_node":
                key = "requested_graph_nodes" if requested else "full_graph_nodes"
            else:
                key = "requested_artifacts" if requested else "visible_worker_reports"
            current = payload.setdefault(key, [])
            assert isinstance(current, list)
            current.append(item.full_content)
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > self.effective_character_limit:
                current.pop()
                continue
            serialized, provider_characters = candidate_serialized, candidate_characters
            included_ids.add(reference.artifact_id)
            included.append(
                {
                    "artifact_id": reference.artifact_id,
                    "reason": item.inclusion_reason,
                }
            )

        omitted = [
            item.reference
            for item in all_evidence
            if item.reference.artifact_id not in included_ids
        ]
        manifest = self._manifest(
            decision_id=decision_id,
            after_event_sequence=after_event_sequence,
            mode="compact",
            payload=payload,
            serialized=serialized,
            provider_characters=provider_characters,
            included=included,
            omitted=omitted,
            aggregated=aggregated,
            requested_artifacts=requested_artifacts,
            requested_graph_nodes=requested_graph_nodes,
        )
        return CoordinatorContextBuild(payload, serialized, manifest)

    def _build_indexed(
        self,
        *,
        decision_id: int,
        after_event_sequence: int,
        indexed_base: dict[str, object],
        events: list[dict[str, object]],
        assignment_table: list[dict[str, object]],
        evidence: list[CoordinatorEvidenceItem],
        aggregated: list[dict[str, object]],
        requested_artifacts: list[str],
        requested_graph_nodes: list[str],
    ) -> CoordinatorContextBuild:
        """Build an emergency indexed view when even the ordinary compact base is too large."""

        payload: dict[str, object] = {
            **indexed_base,
            "context_mode": "indexed",
            "context_contract": {
                "raw_evidence_is_authoritative": True,
                "references_are_bound_to_frozen_sha256": True,
                "historical_state_is_an_index_not_canonical_evidence": True,
                "request_omitted_evidence_with": [
                    "requested_artifact_ids",
                    "requested_graph_node_ids",
                ],
            },
            "assignment_lifecycle": [],
            "unacknowledged_events": [],
            "report_summaries": [],
            "graph_node_summaries": [],
            "visible_worker_reports": [],
            "full_graph_nodes": [],
            "requested_artifacts": [],
            "requested_graph_nodes": [],
            "artifact_catalog": [],
            "indexed_omissions": [],
        }
        serialized, provider_characters = self._measure(payload)
        if provider_characters > self.effective_character_limit:
            raise CoordinatorContextBudgetExhausted(
                limit=self.effective_character_limit,
                required=provider_characters,
            )
        omission_reserve = min(4_096, max(self.effective_character_limit // 20, 512))
        packing_limit = max(provider_characters, self.effective_character_limit - omission_reserve)

        omitted_state: list[dict[str, object]] = []

        def integer_value(value: object) -> int:
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        status_priority = {"running": 0, "queued": 1, "completed": 2}
        ordered_assignments = sorted(
            assignment_table,
            key=lambda assignment_item: (
                status_priority.get(str(assignment_item.get("status")), 3),
                -integer_value(assignment_item.get("completed_event_sequence")),
                str(assignment_item.get("assignment_id", "")),
            ),
        )
        lifecycle = payload["assignment_lifecycle"]
        assert isinstance(lifecycle, list)
        open_assignments = [
            item for item in ordered_assignments if item.get("status") in {"running", "queued"}
        ]
        for assignment_item in open_assignments:
            lifecycle.append(assignment_item)
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > packing_limit:
                lifecycle.pop()
                continue
            serialized, provider_characters = candidate_serialized, candidate_characters

        indexed_events = [_compact_event(event) for event in events]
        selected_events: list[dict[str, object]] = []
        for event in reversed(indexed_events):
            selected_events.append(event)
            selected_events.sort(
                key=lambda event_item: integer_value(
                    event_item.get("sequence", event_item.get("first_sequence", 0))
                )
            )
            payload["unacknowledged_events"] = selected_events
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > packing_limit:
                selected_events.remove(event)
                payload["unacknowledged_events"] = selected_events
                continue
            serialized, provider_characters = candidate_serialized, candidate_characters
        if len(selected_events) < len(indexed_events):
            omitted_state.append(
                {
                    "section": "unacknowledged_events",
                    "included": len(selected_events),
                    "omitted": len(indexed_events) - len(selected_events),
                    "recovery": "Read immutable research/events/*.json evidence by sequence.",
                }
            )

        ordered = sorted(
            evidence,
            key=lambda evidence_item: (
                evidence_item.priority,
                evidence_item.reference.artifact_id,
            ),
        )

        def evidence_requested(evidence_item: CoordinatorEvidenceItem) -> bool:
            reference = evidence_item.reference
            return reference.artifact_id in requested_artifacts or (
                reference.graph_node_id is not None
                and reference.graph_node_id in requested_graph_nodes
            )

        included: list[dict[str, str]] = []
        included_ids: set[str] = set()

        def add_full_evidence(evidence_item: CoordinatorEvidenceItem) -> None:
            nonlocal serialized, provider_characters
            reference = evidence_item.reference
            requested = evidence_requested(evidence_item)
            if reference.kind == "graph_node":
                key = "requested_graph_nodes" if requested else "full_graph_nodes"
            else:
                key = "requested_artifacts" if requested else "visible_worker_reports"
            current = payload[key]
            assert isinstance(current, list)
            current.append(evidence_item.full_content)
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > packing_limit:
                current.pop()
                return
            serialized, provider_characters = candidate_serialized, candidate_characters
            included_ids.add(reference.artifact_id)
            included.append(
                {
                    "artifact_id": reference.artifact_id,
                    "reason": evidence_item.inclusion_reason,
                }
            )

        # Explicit retrieval requests are serviced before lower-priority history.
        for evidence_item in ordered:
            if evidence_requested(evidence_item):
                add_full_evidence(evidence_item)

        # Scientific summaries outrank closed assignment bookkeeping.
        for evidence_item in ordered:
            key = (
                "graph_node_summaries"
                if evidence_item.reference.kind == "graph_node"
                else "report_summaries"
            )
            current = payload[key]
            assert isinstance(current, list)
            current.append(evidence_item.summary)
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > packing_limit:
                current.pop()
                continue
            serialized, provider_characters = candidate_serialized, candidate_characters

        # Then include high-priority complete evidence that still fits.
        for evidence_item in ordered:
            if not evidence_requested(evidence_item) and evidence_item.priority < 10:
                add_full_evidence(evidence_item)

        # Summaries already carry path/hash pairs. The separate catalog is useful but
        # lower priority than the scientific content and requested full evidence.
        catalog = payload["artifact_catalog"]
        assert isinstance(catalog, list)
        for evidence_item in ordered:
            catalog.append(evidence_item.reference.model_dump(mode="json"))
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > packing_limit:
                catalog.pop()
                continue
            serialized, provider_characters = candidate_serialized, candidate_characters

        historical_assignments = [
            item for item in ordered_assignments if item.get("status") not in {"running", "queued"}
        ]
        for assignment_item in historical_assignments:
            lifecycle.append(assignment_item)
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters > packing_limit:
                lifecycle.pop()
                continue
            serialized, provider_characters = candidate_serialized, candidate_characters
        if len(lifecycle) < len(ordered_assignments):
            omitted_state.append(
                {
                    "section": "assignment_lifecycle",
                    "included": len(lifecycle),
                    "omitted": len(ordered_assignments) - len(lifecycle),
                    "recovery": "Use report summaries and the authenticated scheduler index.",
                }
            )

        omitted = [
            evidence_item.reference
            for evidence_item in evidence
            if evidence_item.reference.artifact_id not in included_ids
        ]
        payload["indexed_omissions"] = omitted_state
        candidate_serialized, candidate_characters = self._measure(payload)
        if candidate_characters <= self.effective_character_limit:
            serialized, provider_characters = candidate_serialized, candidate_characters
        elif omitted_state:
            payload["indexed_omissions"] = [
                {
                    "section": "multiple_indexed_sections",
                    "omitted": sum(integer_value(item.get("omitted")) for item in omitted_state),
                    "recovery": "Inspect the context manifest and canonical scheduler ledger.",
                }
            ]
            candidate_serialized, candidate_characters = self._measure(payload)
            if candidate_characters <= self.effective_character_limit:
                serialized, provider_characters = candidate_serialized, candidate_characters
            else:
                payload["indexed_omissions"] = []
                serialized, provider_characters = self._measure(payload)
        manifest = self._manifest(
            decision_id=decision_id,
            after_event_sequence=after_event_sequence,
            mode="indexed",
            payload=payload,
            serialized=serialized,
            provider_characters=provider_characters,
            included=included,
            omitted=omitted,
            aggregated=aggregated,
            requested_artifacts=requested_artifacts,
            requested_graph_nodes=requested_graph_nodes,
            omitted_state=omitted_state,
        )
        return CoordinatorContextBuild(payload, serialized, manifest)

    def _manifest(
        self,
        *,
        decision_id: int,
        after_event_sequence: int,
        mode: Literal["normal", "compact", "indexed"],
        payload: Mapping[str, object],
        serialized: str,
        provider_characters: int,
        included: list[dict[str, str]],
        omitted: list[CoordinatorArtifactReference],
        aggregated: list[dict[str, object]],
        requested_artifacts: list[str],
        requested_graph_nodes: list[str],
        omitted_state: list[dict[str, object]] | None = None,
    ) -> CoordinatorContextManifest:
        del payload
        return CoordinatorContextManifest(
            decision_id=decision_id,
            after_event_sequence=after_event_sequence,
            mode=mode,
            configured_character_limit=self.configured_character_limit,
            effective_character_limit=self.effective_character_limit,
            serialized_payload_characters=len(serialized),
            serialized_provider_input_characters=provider_characters,
            estimated_input_tokens=(provider_characters + 3) // 4,
            payload_sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            included_full_artifacts=included,
            omitted_artifacts=omitted,
            aggregated_event_groups=aggregated,
            requested_artifact_ids=requested_artifacts,
            requested_graph_node_ids=requested_graph_nodes,
            omitted_state_sections=list(omitted_state or []),
        )


__all__ = [
    "CoordinatorArtifactReference",
    "CoordinatorContextBudgetExhausted",
    "CoordinatorContextBuild",
    "CoordinatorContextBuilder",
    "CoordinatorContextManifest",
    "CoordinatorEvidenceItem",
    "serialize_coordinator_payload",
]
