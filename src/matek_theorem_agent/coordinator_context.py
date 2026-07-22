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

    schema_version: Literal[1] = 1
    decision_id: int = Field(ge=1)
    after_event_sequence: int = Field(ge=0)
    mode: Literal["normal", "compact"]
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


@dataclass(frozen=True)
class CoordinatorContextBuild:
    payload: dict[str, object]
    serialized_input: str
    manifest: CoordinatorContextManifest


class CoordinatorContextBudgetExhausted(RuntimeError):
    """Mandatory coordinator state cannot fit the configured transport budget."""

    def __init__(self, *, limit: int, required: int) -> None:
        self.limit = limit
        self.required = required
        super().__init__(
            "CONTEXT_BUDGET_EXHAUSTED: mandatory coordinator state requires "
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
            raise CoordinatorContextBudgetExhausted(
                limit=self.effective_character_limit,
                required=provider_characters,
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

    def _manifest(
        self,
        *,
        decision_id: int,
        after_event_sequence: int,
        mode: Literal["normal", "compact"],
        payload: Mapping[str, object],
        serialized: str,
        provider_characters: int,
        included: list[dict[str, str]],
        omitted: list[CoordinatorArtifactReference],
        aggregated: list[dict[str, object]],
        requested_artifacts: list[str],
        requested_graph_nodes: list[str],
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
