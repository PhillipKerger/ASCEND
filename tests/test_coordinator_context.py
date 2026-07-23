from __future__ import annotations

from matek_theorem_agent.coordinator_context import (
    CoordinatorArtifactReference,
    CoordinatorContextBudgetExhausted,
    CoordinatorContextBuilder,
    CoordinatorEvidenceItem,
)


def _report(index: int, *, priority: int, status: str = "progress") -> CoordinatorEvidenceItem:
    assignment_id = f"worker-{index:02d}"
    digest = f"{index:064x}"
    return CoordinatorEvidenceItem(
        reference=CoordinatorArtifactReference(
            artifact_id=f"worker-report:{assignment_id}",
            kind="worker_report",
            relative_path=f"research/workers/{assignment_id}.json",
            sha256=digest,
            assignment_id=assignment_id,
        ),
        summary={
            "assignment_id": assignment_id,
            "status": status,
            "mechanism": f"mechanism {index}",
            "formal_results": [f"Lemma {index}"],
            "counterexamples": [],
            "exact_gap": f"gap {index}",
            "dependencies": [],
            "path": f"research/workers/{assignment_id}.json",
            "sha256": digest,
        },
        full_content={
            "assignment_id": assignment_id,
            "status": status,
            "proof_content": (f"complete mathematical report {index} " * 800),
        },
        priority=priority,
        inclusion_reason=f"priority {priority}",
    )


def test_large_coordinator_context_is_bounded_prioritized_and_addressable() -> None:
    reports = [
        _report(
            index,
            priority=(0 if index == 31 else 1 if index == 7 else 3 if index == 8 else 10),
            status="candidate_complete" if index == 7 else "progress",
        )
        for index in range(32)
    ]
    repeated_events = [
        {
            "schema_version": 1,
            "sequence": index + 1,
            "kind": "graph_mutation_rejected",
            "assignment_id": f"worker-{index:02d}",
            "artifact": f"issues/issue-{index:02d}.json",
            "detail": ["Repair the optional graph proposal."],
        }
        for index in range(12)
    ]
    base = {
        "compiled_prompt": "Exact unchanged research prompt.",
        "claim_contract": {"conclusion": "P"},
        "decision_id": 8,
        "after_event_sequence": 12,
    }
    normal = {
        **base,
        "unacknowledged_events": repeated_events,
        "visible_worker_reports": [item.full_content for item in reports],
    }
    builder = CoordinatorContextBuilder(
        configured_character_limit=120_000,
        provider_input_characters=lambda serialized: len(serialized) + 2_048,
    )

    built = builder.build(
        decision_id=8,
        after_event_sequence=12,
        normal_payload=normal,
        compact_base=base,
        events=repeated_events,
        assignment_table=[
            {
                "assignment_id": f"worker-{index:02d}",
                "status": "completed",
                "approach_family": f"family-{index % 8}",
                "objective": f"route {index}",
                "artifact_id": f"worker-report:worker-{index:02d}",
            }
            for index in range(32)
        ],
        report_evidence=reports,
        graph_memory=None,
        requested_artifact_ids=["worker-report:worker-31"],
    )

    assert built.manifest.mode == "compact"
    assert built.manifest.serialized_provider_input_characters <= 120_000
    assert len(built.payload["report_summaries"]) == 32
    included_ids = [item["assignment_id"] for item in built.payload["visible_worker_reports"]]
    assert included_ids.index("worker-07") < included_ids.index("worker-08")
    requested = built.payload["requested_artifacts"]
    assert isinstance(requested, list)
    assert requested[0]["assignment_id"] == "worker-31"
    assert built.manifest.omitted_artifacts
    assert all(item.reference.relative_path and item.reference.sha256 for item in reports[0:1])
    aggregate = built.manifest.aggregated_event_groups[0]
    assert aggregate["count"] == 12
    assert len(aggregate["affected_assignment_ids"]) == 12
    assert len(aggregate["issue_paths"]) == 12


def test_immutable_prompt_contract_fails_truthfully_when_it_cannot_fit() -> None:
    builder = CoordinatorContextBuilder(configured_character_limit=100_000)
    mandatory = {
        "compiled_prompt": "mandatory theorem statement " * 8_000,
        "claim_contract": {"conclusion": "P"},
        "decision_id": 1,
        "after_event_sequence": 0,
    }

    try:
        builder.build(
            decision_id=1,
            after_event_sequence=0,
            normal_payload=mandatory,
            compact_base=mandatory,
            events=[],
            assignment_table=[],
            report_evidence=[],
            graph_memory=None,
            force_compact=True,
        )
    except CoordinatorContextBudgetExhausted as exc:
        assert exc.limit == 100_000
        assert exc.required > exc.limit
        assert "CONTEXT_BUDGET_EXHAUSTED" in str(exc)
    else:  # pragma: no cover - the fixture must exceed its explicit hard budget
        raise AssertionError("mandatory oversized context unexpectedly fit")


def test_oversized_scheduler_state_falls_back_to_bounded_indexed_context() -> None:
    builder = CoordinatorContextBuilder(configured_character_limit=100_000)
    base = {
        "compiled_prompt": "Exact unchanged research prompt.",
        "claim_contract": {"conclusion": "P"},
        "decision_id": 41,
        "after_event_sequence": 2_000,
    }
    assignments = [
        {
            "assignment_id": f"worker-{index:04d}",
            "status": "running" if index >= 1_995 else "completed",
            "approach_family": f"family-{index % 8}",
            "objective": "Large historical objective " * 20,
            "completed_event_sequence": index,
        }
        for index in range(2_000)
    ]
    events = [
        {
            "schema_version": 1,
            "sequence": index,
            "kind": "worker_report_accepted",
            "assignment_id": f"worker-{index:04d}",
            "artifact": f"workers/worker-{index:04d}.json",
            "artifact_sha256": f"{index:064x}",
            "detail": ["Detailed historical event prose " * 30],
        }
        for index in range(1, 2_001)
    ]

    built = builder.build(
        decision_id=41,
        after_event_sequence=2_000,
        normal_payload={**base, "assignment_lifecycle": assignments, "events": events},
        compact_base={**base, "large_registry": "registry " * 80_000},
        indexed_base={
            **base,
            "scheduler_state_index": {
                "assignment_count": len(assignments),
                "canonical_path": "research/coordinator/state.json",
            },
        },
        events=events,
        assignment_table=assignments,
        report_evidence=[],
        graph_memory=None,
        force_compact=True,
    )

    assert built.manifest.mode == "indexed"
    assert built.manifest.serialized_provider_input_characters <= 100_000
    assert built.payload["context_mode"] == "indexed"
    assert built.payload["scheduler_state_index"]["assignment_count"] == 2_000
    selected_events = built.payload["unacknowledged_events"]
    assert selected_events
    assert selected_events[-1]["sequence"] == 2_000
    lifecycle = built.payload["assignment_lifecycle"]
    assert lifecycle
    assert lifecycle[0]["status"] == "running"
    assert built.manifest.omitted_state_sections
    assert built.payload["indexed_omissions"]
