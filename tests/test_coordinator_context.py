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


def test_mandatory_coordinator_state_fails_truthfully_when_it_cannot_fit() -> None:
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
