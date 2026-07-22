from __future__ import annotations

import json
import re
import shutil
import unicodedata
from collections.abc import Collection, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import ModelSettings
from ..execution.base import CommandRequest, CommandResult, ExecutionBackend
from ..openai_client import ModelClient, ModelRequest
from ..source_identifiers import source_identifiers, tool_metadata_source_identifiers
from ..source_provenance import (
    IdentifierVerifier,
    SourceEvidenceClaim,
    SourceVerificationRecord,
    SourceVerificationReport,
    SourceVerificationStatus,
    provider_verification_records,
)
from ..verification import (
    classify_latex_result,
    parse_bibtex,
    validate_bibliography,
    validate_matek_ai_usage,
)
from .common import (
    ArtifactManifest,
    CallManifest,
    StageGateError,
    StageValidationError,
    atomic_write_json,
    atomic_write_text,
    build_artifact_manifest,
    ensure_stage_directory,
    project_resource,
    sha256_json,
    sha256_text,
)
from .research import (
    ResearchAcceptanceGate,
    ResearchOutcome,
    ResearchResult,
)


class BibliographyStatus(StrEnum):
    VERIFIED = "verified"
    CORRECTIONS_REQUIRED = "corrections_required"
    REJECTED = "rejected"


class BibliographyEntryStatus(StrEnum):
    VERIFIED = "verified"
    CORRECTED = "corrected"
    AMBIGUOUS = "ambiguous"
    NONEXISTENT = "nonexistent"


class ManuscriptOutcome(StrEnum):
    COMPILED = "compiled"
    DRAFT_WITH_WARNINGS = "draft_with_warnings"
    PUBLICATION_BLOCKED = "publication_blocked"
    CONTENT_REJECTED = "content_rejected"
    BIBLIOGRAPHY_REJECTED = "bibliography_rejected"
    LATEX_FAILED = "latex_failed"


class ManuscriptStatus(StrEnum):
    PUBLICATION_READY = "PUBLICATION_READY"
    DRAFT_WITH_WARNINGS = "DRAFT_WITH_WARNINGS"
    PUBLICATION_BLOCKED = "PUBLICATION_BLOCKED"


class PublicationStatus(StrEnum):
    READY = "READY"
    BLOCKED_METADATA = "BLOCKED_METADATA"
    BLOCKED_CONTENT = "BLOCKED_CONTENT"
    BLOCKED_BIBLIOGRAPHY = "BLOCKED_BIBLIOGRAPHY"
    BLOCKED_LATEX = "BLOCKED_LATEX"
    BLOCKED_INTEGRITY = "BLOCKED_INTEGRITY"


class ManuscriptFindingSeverity(StrEnum):
    HARD_FAILURE = "hard_failure"
    REPAIRABLE = "repairable"
    PUBLICATION_WARNING = "publication_warning"


class ManuscriptFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: ManuscriptFindingSeverity
    message: str
    repair: str | None = None

    @field_validator("code", "message")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("manuscript finding code and message must not be blank")
        return normalized


class IntroductionCoverage(BaseModel):
    """Structured claims and source keys supporting the introduction content gate."""

    model_config = ConfigDict(extra="forbid")

    related_work_excerpt: str
    difference_from_prior_work_excerpt: str
    advance_over_prior_work_excerpt: str
    citation_keys: list[str]

    @field_validator(
        "related_work_excerpt",
        "difference_from_prior_work_excerpt",
        "advance_over_prior_work_excerpt",
    )
    @classmethod
    def excerpt_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("introduction coverage excerpts must not be empty")
        return value.strip()


class FrozenClaimFidelity(BaseModel):
    """Machine-checkable binding from a draft to its frozen scientific inputs."""

    model_config = ConfigDict(extra="forbid")

    candidate_sha256: str
    claim_contract_sha256: str
    exact_theorem: str
    manuscript_main_claim: str
    exact_match: bool

    @field_validator("candidate_sha256", "claim_contract_sha256")
    @classmethod
    def digest_is_sha256(cls, value: str) -> str:
        normalized = value.casefold().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise ValueError("frozen claim digests must be SHA-256 values")
        return normalized

    @field_validator("exact_theorem", "manuscript_main_claim")
    @classmethod
    def claim_text_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("frozen and manuscript theorem statements must not be empty")
        return value.strip()


class ManuscriptClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    proof: str


class ProofDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    dependencies: list[str]


class ManuscriptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_tex: str
    references_bib: str
    claims: list[ManuscriptClaim]
    proof_dependency_graph: list[ProofDependency]
    introduction_coverage: IntroductionCoverage
    frozen_claim_fidelity: FrozenClaimFidelity

    @field_validator("proof_dependency_graph", mode="before")
    @classmethod
    def accept_legacy_dependency_map(cls, value: object) -> object:
        if isinstance(value, dict):
            return [
                {"claim": str(claim), "dependencies": dependencies}
                for claim, dependencies in value.items()
            ]
        return value

    @field_validator("paper_tex", "references_bib")
    @classmethod
    def source_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manuscript source must not be empty")
        return value


class BibliographyCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    corrected_value: str


class BibliographyEntryAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_key: str
    status: BibliographyEntryStatus
    exists: bool
    exact_title_verified: bool
    authors_verified: bool
    year_verified: bool
    venue_or_status_verified: bool
    stable_identifier_checked: bool
    characterization_supported: bool
    theorem_hypotheses_supported: bool
    authoritative_evidence: list[SourceEvidenceClaim] = Field(default_factory=list)
    corrections: list[BibliographyCorrection] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_evidence(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        key = str(value.get("citation_key") or "").strip()
        evidence = value.get("authoritative_evidence")
        if isinstance(evidence, list):
            value = dict(value)
            value["authoritative_evidence"] = [
                {"claim": item, "source_ids": [key]} if isinstance(item, str) else item
                for item in evidence
            ]
        return value

    @field_validator("corrections", mode="before")
    @classmethod
    def accept_legacy_correction_map(cls, value: object) -> object:
        if isinstance(value, dict):
            return [
                {"field": str(field), "corrected_value": corrected_value}
                for field, corrected_value in value.items()
            ]
        return value

    @property
    def fully_verified(self) -> bool:
        return (
            self.status == BibliographyEntryStatus.VERIFIED
            and self.exists
            and self.exact_title_verified
            and self.authors_verified
            and self.year_verified
            and self.venue_or_status_verified
            and self.stable_identifier_checked
            and self.characterization_supported
            and self.theorem_hypotheses_supported
            and bool(self.authoritative_evidence)
            and all(
                isinstance(item, SourceEvidenceClaim) and self.citation_key in item.source_ids
                for item in self.authoritative_evidence
            )
        )


class RelatedWorkClaimAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    citation_keys: list[str] = Field(default_factory=list)
    supported: bool
    evidence: list[SourceEvidenceClaim] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_evidence(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        citation_keys = [
            str(item).strip() for item in value.get("citation_keys", []) if str(item).strip()
        ]
        evidence = value.get("evidence")
        if isinstance(evidence, list):
            value = dict(value)
            value["evidence"] = [
                {"claim": item, "source_ids": citation_keys} if isinstance(item, str) else item
                for item in evidence
            ]
        return value


class BibliographyAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BibliographyStatus
    entries: list[BibliographyEntryAudit]
    claim_checks: list[RelatedWorkClaimAudit]
    blocking_issues: list[str]
    correction_plan: list[str] = Field(default_factory=list)


class RelatedWorkValidation(BaseModel):
    passed: bool
    has_related_work_section: bool
    has_introduction_section: bool = False
    has_ai_usage_statement: bool = False
    ai_usage_disclosure_verified: bool = False
    matek_repository_citation_key: str | None = None
    matek_whitepaper_citation_key: str | None = None
    matek_technical_report_citation_key: str | None = None
    matek_whitepaper_citation_pending: bool = False
    introduction_word_count: int = 0
    related_work_word_count: int = 0
    introduction_coverage_verified: bool = False
    frozen_claim_fidelity_verified: bool = False
    cited_keys: list[str]
    bibliography_keys: list[str]
    missing_bibliography_keys: list[str]
    issues: list[str]
    findings: list[ManuscriptFinding] = Field(default_factory=list)


class LatexBuildResult(BaseModel):
    passed: bool
    argv: list[str]
    exit_code: int | None
    diagnostics: list[str]
    pdf_path: Path | None = None


class ManuscriptResult(BaseModel):
    outcome: ManuscriptOutcome
    draft: ManuscriptDraft
    bibliography_audit: BibliographyAudit | None
    bibliography_verified: bool
    related_work: RelatedWorkValidation
    latex_build: LatexBuildResult | None
    correction_cycles: int
    research_gate: ResearchAcceptanceGate
    manuscript_status: ManuscriptStatus = ManuscriptStatus.PUBLICATION_BLOCKED
    publication_status: PublicationStatus = PublicationStatus.BLOCKED_CONTENT
    findings: list[ManuscriptFinding] = Field(default_factory=list)
    selected_draft_cycle: int = 0
    revision_rounds_exhausted: bool = False
    artifacts: ArtifactManifest = Field(default_factory=ArtifactManifest)
    calls: CallManifest

    @property
    def has_terminating_failure(self) -> bool:
        return any(
            finding.severity is ManuscriptFindingSeverity.HARD_FAILURE for finding in self.findings
        )

    @property
    def permits_formalization(self) -> bool:
        if not self.research_gate.accepted:
            return False
        if not self.findings:
            return self.outcome is ManuscriptOutcome.COMPILED
        return not self.has_terminating_failure

    @property
    def passed_lean_gate(self) -> bool:
        """Compatibility alias for the formalization-entry decision."""

        return self.permits_formalization


class ManuscriptDraftValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle: int
    related_work: RelatedWorkValidation
    bibliography_audit: BibliographyAudit | None
    bibliography_gate_issues: list[str]
    latex_build: LatexBuildResult | None
    findings: list[ManuscriptFinding]


_RELATED_SECTION = re.compile(
    r"\\(?:sub)*section\*?\s*\{[^}]*related(?:\s+and\s+existing)?\s+work[^}]*\}",
    re.IGNORECASE,
)
_INTRODUCTION_SECTION = re.compile(
    r"\\section\*?\s*\{\s*introduction\s*\}",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\\cite[a-zA-Z*]*\s*(?:\[[^]]*\]\s*)*\{([^}]+)\}")
_BIB_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,", re.IGNORECASE)
_SECTION_START = re.compile(r"\\(?:sub)*section\*?\s*\{", re.IGNORECASE)
_MINIMUM_INTRODUCTION_WORDS = 35
_MINIMUM_RELATED_WORK_WORDS = 25
_MINIMUM_COVERAGE_EXCERPT_WORDS = 6
_PROHIBITED_TEX_COMMANDS = re.compile(
    r"\\(?:"
    r"(?:immediate\s*)?\\?write\s*18|"
    r"openin|openout|newread|newwrite|read|write|input|include|includegraphics|"
    r"lstinputlisting|verbatiminput|directlua|latelua|special|pdfximage|pdffiledump|"
    r"catcode|csname|scantokens|CatchFileDef|InputIfFileExists|IfFileExists|ShellEscape"
    r")\b",
    re.IGNORECASE,
)
_DANGEROUS_TEX_PACKAGES = frozenset(
    {"attachfile", "attachfile2", "catchfile", "currfile", "embedfile", "minted", "shellesc"}
)
_SHELL_ESCAPE_FLAGS = frozenset(
    {"-shell-escape", "--shell-escape", "-enable-write18", "--enable-write18"}
)
_DELIBERATE_TEX_FAILURE = re.compile(
    r"\\(?:PackageError|ClassError|GenericError|errmessage|stop)\b",
    re.IGNORECASE,
)
_TEX_MACRO_DEFINITION = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand)\s*\{\\([A-Za-z@]+)\}"
    r"(?:\s*\[[0-9]+\])?\s*\{([^{}]*)\}",
)
_SEMANTIC_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "this",
        "to",
        "we",
        "with",
    }
)


def _section_body(paper_tex: str, section_match: re.Match[str] | None) -> str:
    if section_match is None:
        return ""
    following_section = _SECTION_START.search(paper_tex, section_match.end())
    end = following_section.start() if following_section is not None else len(paper_tex)
    return paper_tex[section_match.end() : end]


def _word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z]{2,}", value))


def _normalized_excerpt(value: str) -> str:
    return " ".join(value.replace("~", " ").split()).casefold()


def _expand_simple_tex_macros(value: str, macro_source: str) -> str:
    macros = {
        name: replacement for name, replacement in _TEX_MACRO_DEFINITION.findall(macro_source)
    }
    expanded = value
    for _ in range(4):
        previous = expanded
        for name, replacement in macros.items():

            def replace_macro(_: re.Match[str], replacement_text: str = replacement) -> str:
                return replacement_text

            expanded = re.sub(
                rf"\\{re.escape(name)}\b",
                replace_macro,
                expanded,
            )
        if expanded == previous:
            break
    return expanded


def _canonical_claim_text(value: str, *, macro_source: str = "") -> str:
    normalized = unicodedata.normalize("NFKC", _expand_simple_tex_macros(value, macro_source))
    substitutions = {
        r"\neq": "≠",
        r"\ne": "≠",
        r"\leq": "≤",
        r"\le": "≤",
        r"\geq": "≥",
        r"\ge": "≥",
        r"\in": "∈",
        r"\forall": "∀",
        r"\exists": "∃",
        r"\chi": "χ",
        r"\prime": "'",
    }
    for tex, rendered in substitutions.items():
        normalized = normalized.replace(tex, rendered)
    normalized = re.sub(
        r"\\(?:operatorname|mathrm|mathbf|mathit|mathsf|text|mbox)\s*\{([^{}]*)\}",
        r"\1",
        normalized,
    )
    normalized = re.sub(r"\\(?:left|right|quad|qquad)\b|\\[,;!]", "", normalized)
    normalized = normalized.replace("\\(", "").replace("\\)", "")
    normalized = normalized.replace("\\[", "").replace("\\]", "")
    normalized = normalized.replace("$", "").replace("{", "").replace("}", "")
    normalized = normalized.replace("~", " ").casefold()
    return re.sub(r"[^0-9a-z_χ∀∃∈≤≥≠'=()\[\]+\-*/.,:]", "", normalized)


def _semantic_tokens(value: str) -> set[str]:
    prose = re.sub(r"\\cite[a-zA-Z*]*\s*(?:\[[^]]*\]\s*)*\{[^}]+\}", " ", value)
    prose = re.sub(r"\\[A-Za-z@]+", " ", prose)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", unicodedata.normalize("NFKC", prose).casefold())
        if token not in _SEMANTIC_STOP_WORDS
    }
    return tokens


def _semantic_claim_present(claim: str, body: str) -> bool:
    claim_tokens = _semantic_tokens(claim)
    body_tokens = _semantic_tokens(body)
    if not claim_tokens:
        return False
    overlap = len(claim_tokens.intersection(body_tokens)) / len(claim_tokens)
    return overlap >= 0.45


def _strip_tex_comments(value: str) -> str:
    lines: list[str] = []
    for line in value.splitlines():
        comment_at: int | None = None
        for index, character in enumerate(line):
            if character != "%":
                continue
            preceding = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                preceding += 1
                cursor -= 1
            if preceding % 2 == 0:
                comment_at = index
                break
        lines.append(line if comment_at is None else line[:comment_at])
    return "\n".join(lines)


def validate_tex_safety(paper_tex: str, references_bib: str) -> list[str]:
    """Reject generated TeX primitives that can read files or invoke processes."""

    issues: list[str] = []
    for label, value in (("paper.tex", paper_tex), ("references.bib", references_bib)):
        source = _strip_tex_comments(value)
        match = _PROHIBITED_TEX_COMMANDS.search(source)
        if match is not None:
            issues.append(f"{label} contains prohibited TeX escape command {match.group(0)!r}.")
        if "^^" in source:
            issues.append(f"{label} contains prohibited TeX character-code obfuscation.")
        if re.search(r"(?i)(?:run|file|javascript)\s*:", source):
            issues.append(f"{label} contains a prohibited active or local-file URI.")

    paper_source = _strip_tex_comments(paper_tex)
    deliberate_failure = _DELIBERATE_TEX_FAILURE.search(paper_source)
    if deliberate_failure is not None:
        issues.append(
            "paper.tex contains a deliberate LaTeX build-failure command "
            f"{deliberate_failure.group(0)!r}."
        )
    for match in re.finditer(
        r"(?i)\\(?:usepackage|RequirePackage)(?:\[[^]]*\])?\s*\{([^}]*)\}", paper_source
    ):
        packages = {item.strip().casefold() for item in match.group(1).split(",")}
        unsafe = packages.intersection(_DANGEROUS_TEX_PACKAGES)
        if unsafe:
            issues.append(
                "paper.tex requests prohibited TeX packages: " + ", ".join(sorted(unsafe))
            )
        if any(not re.fullmatch(r"[a-z0-9_-]+", package) for package in packages):
            issues.append("paper.tex contains a package name with a path or unsafe characters.")
    for command, required in (("bibliography", "references"), ("addbibresource", "references.bib")):
        for match in re.finditer(rf"(?i)\\{command}\s*\{{([^}}]*)\}}", paper_source):
            values = {item.strip() for item in match.group(1).split(",")}
            if values != {required}:
                issues.append(
                    f"paper.tex may use \\{command} only with the run-local {required!r} file."
                )
    for command in ("documentclass", "bibliographystyle"):
        for match in re.finditer(rf"(?i)\\{command}(?:\[[^]]*\])?\s*\{{([^}}]*)\}}", paper_source):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", match.group(1).strip()):
                issues.append(
                    f"paper.tex contains a \\{command} value with a path or unsafe characters."
                )
    return list(dict.fromkeys(issues))


def harden_latex_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Return a shell-disabled deterministic TeX command or reject an unsafe command."""

    lowered = {part.casefold() for part in command[1:]}
    forbidden = sorted(lowered.intersection(_SHELL_ESCAPE_FLAGS))
    if forbidden:
        raise StageValidationError("LaTeX shell escape cannot be enabled: " + ", ".join(forbidden))
    executable = Path(command[0]).name.casefold()
    result = list(command)
    if executable == "latexmk":
        if "-no-shell-escape" not in lowered:
            result.insert(1, "-no-shell-escape")
        if "-norc" not in lowered:
            result.insert(1, "-norc")
    elif executable in {"latex", "pdflatex", "xelatex", "lualatex"}:
        if "-no-shell-escape" not in lowered:
            result.insert(1, "-no-shell-escape")
    elif executable != "tectonic" and "-no-shell-escape" not in lowered:
        raise StageValidationError(
            "A custom LaTeX command must explicitly include -no-shell-escape."
        )
    return tuple(result)


def validate_related_work(
    paper_tex: str,
    references_bib: str,
    *,
    introduction_coverage: IntroductionCoverage | None = None,
    frozen_claim_fidelity: FrozenClaimFidelity | None = None,
    expected_candidate_sha256: str | None = None,
    expected_claim_contract_sha256: str | None = None,
    expected_exact_theorem: str | None = None,
) -> RelatedWorkValidation:
    """Classify deterministic content findings without requiring byte-for-byte prose."""

    findings: list[ManuscriptFinding] = []

    def add(
        code: str,
        severity: ManuscriptFindingSeverity,
        message: str,
        repair: str | None = None,
    ) -> None:
        findings.append(
            ManuscriptFinding(code=code, severity=severity, message=message, repair=repair)
        )

    cited = sorted(
        {
            key.strip()
            for group in _CITATION.findall(paper_tex)
            for key in group.split(",")
            if key.strip()
        }
    )
    bibliography = sorted({key.strip() for key in _BIB_ENTRY.findall(references_bib)})
    bibliography_report = validate_bibliography(paper_tex, references_bib)
    ai_usage_report = validate_matek_ai_usage(paper_tex, references_bib)
    missing = sorted(set(cited) - set(bibliography))
    section_match = _RELATED_SECTION.search(paper_tex)
    introduction_match = _INTRODUCTION_SECTION.search(paper_tex)
    has_section = section_match is not None
    has_introduction = introduction_match is not None
    introduction_body = _section_body(paper_tex, introduction_match)
    related_body = _section_body(paper_tex, section_match)
    introduction_words = _word_count(introduction_body)
    related_words = _word_count(related_body)
    for message in validate_tex_safety(paper_tex, references_bib):
        code = (
            "deliberate_latex_failure"
            if "deliberate LaTeX build-failure" in message
            else "unsafe_tex_output"
        )
        add(
            code,
            ManuscriptFindingSeverity.HARD_FAILURE,
            message,
            "Remove the unsafe or deliberate failure command before any TeX execution.",
        )
    for issue in ai_usage_report.issues:
        severity = (
            ManuscriptFindingSeverity.HARD_FAILURE
            if issue.code == "fabricated_or_placeholder_matek_citation"
            else ManuscriptFindingSeverity.REPAIRABLE
        )
        add(issue.code, severity, issue.message, "Correct the AI-usage statement or citation.")
    for warning in ai_usage_report.warnings:
        add(
            warning.code,
            ManuscriptFindingSeverity.PUBLICATION_WARNING,
            warning.message,
            "Add the canonical whitepaper citation after its metadata becomes available.",
        )
    if not has_introduction:
        add(
            "missing_introduction_section",
            ManuscriptFindingSeverity.REPAIRABLE,
            "The manuscript has no explicit Introduction section.",
        )
    elif introduction_words < _MINIMUM_INTRODUCTION_WORDS:
        add(
            "short_introduction",
            ManuscriptFindingSeverity.REPAIRABLE,
            "The Introduction is not substantial enough to establish context, difference, "
            f"and advance (found {introduction_words} words; require at least "
            f"{_MINIMUM_INTRODUCTION_WORDS}).",
        )
    if not has_section:
        add(
            "missing_related_work_section",
            ManuscriptFindingSeverity.REPAIRABLE,
            "The manuscript has no explicit Related Work section.",
        )
    if not cited:
        add(
            "missing_source_citations",
            ManuscriptFindingSeverity.REPAIRABLE,
            "The related-work manuscript contains no source citations.",
        )
    if section_match is not None:
        if related_words < _MINIMUM_RELATED_WORK_WORDS:
            add(
                "short_related_work",
                ManuscriptFindingSeverity.REPAIRABLE,
                "The Related Work section has no substantive characterization.",
            )
        if not _CITATION.search(related_body):
            add(
                "related_work_missing_inline_citation",
                ManuscriptFindingSeverity.REPAIRABLE,
                "The Related Work section contains no inline source citation.",
            )

    coverage_verified = (
        introduction_coverage is not None
        and has_introduction
        and introduction_words >= _MINIMUM_INTRODUCTION_WORDS
    )
    if introduction_coverage is None:
        if expected_candidate_sha256 is not None:
            add(
                "missing_introduction_coverage",
                ManuscriptFindingSeverity.REPAIRABLE,
                "The draft has no structured Introduction coverage record.",
            )
        coverage_verified = False
    else:
        excerpts = {
            "related work": introduction_coverage.related_work_excerpt,
            "difference from prior work": introduction_coverage.difference_from_prior_work_excerpt,
            "advance over prior work": introduction_coverage.advance_over_prior_work_excerpt,
        }
        semantic_values: list[frozenset[str]] = []
        for label, excerpt in excerpts.items():
            semantic_values.append(frozenset(_semantic_tokens(excerpt)))
            if _word_count(excerpt) < _MINIMUM_COVERAGE_EXCERPT_WORDS:
                add(
                    f"insubstantial_{label.replace(' ', '_')}",
                    ManuscriptFindingSeverity.REPAIRABLE,
                    f"The structured {label} claim is not substantive.",
                )
                coverage_verified = False
            if not _semantic_claim_present(excerpt, introduction_body):
                add(
                    f"uncovered_{label.replace(' ', '_')}",
                    ManuscriptFindingSeverity.REPAIRABLE,
                    f"The Introduction does not semantically cover the structured {label} claim.",
                )
                coverage_verified = False
        if len(set(semantic_values)) != len(semantic_values):
            add(
                "duplicated_introduction_coverage",
                ManuscriptFindingSeverity.REPAIRABLE,
                "Introduction coverage uses the same claim for distinct scientific roles.",
            )
            coverage_verified = False
        introduction_citations = {
            key.strip()
            for group in _CITATION.findall(introduction_body)
            for key in group.split(",")
            if key.strip()
        }
        coverage_keys = {key.strip() for key in introduction_coverage.citation_keys if key.strip()}
        if not coverage_keys:
            add(
                "introduction_coverage_missing_citations",
                ManuscriptFindingSeverity.REPAIRABLE,
                "The Introduction coverage record cites no source keys.",
            )
            coverage_verified = False
        elif not coverage_keys.issubset(introduction_citations):
            add(
                "introduction_coverage_citation_mismatch",
                ManuscriptFindingSeverity.REPAIRABLE,
                "Introduction coverage cites keys not present inline in the Introduction: "
                + ", ".join(sorted(coverage_keys - introduction_citations)),
            )
            coverage_verified = False

    fidelity_verified = frozen_claim_fidelity is not None
    frozen_expectations = (
        expected_candidate_sha256,
        expected_claim_contract_sha256,
        expected_exact_theorem,
    )
    if any(value is not None for value in frozen_expectations):
        if frozen_claim_fidelity is None:
            add(
                "missing_frozen_claim_fidelity",
                ManuscriptFindingSeverity.HARD_FAILURE,
                "The draft has no structured frozen-claim fidelity record.",
            )
            fidelity_verified = False
        else:
            fidelity = frozen_claim_fidelity
            if fidelity.candidate_sha256 != expected_candidate_sha256:
                add(
                    "candidate_hash_mismatch",
                    ManuscriptFindingSeverity.HARD_FAILURE,
                    "The manuscript candidate hash does not match the accepted proof.",
                )
                fidelity_verified = False
            if fidelity.claim_contract_sha256 != expected_claim_contract_sha256:
                add(
                    "claim_contract_hash_mismatch",
                    ManuscriptFindingSeverity.HARD_FAILURE,
                    "The manuscript claim-contract hash does not match the frozen claim.",
                )
                fidelity_verified = False
            expected_canonical = _canonical_claim_text(
                expected_exact_theorem or "", macro_source=paper_tex
            )
            fidelity_canonical = _canonical_claim_text(
                fidelity.exact_theorem, macro_source=paper_tex
            )
            manuscript_canonical = _canonical_claim_text(
                fidelity.manuscript_main_claim, macro_source=paper_tex
            )
            if fidelity_canonical != expected_canonical:
                add(
                    "frozen_theorem_drift",
                    ManuscriptFindingSeverity.HARD_FAILURE,
                    "The manuscript fidelity record changes the accepted theorem.",
                )
                fidelity_verified = False
            if manuscript_canonical != expected_canonical or not fidelity.exact_match:
                add(
                    "manuscript_claim_drift",
                    ManuscriptFindingSeverity.HARD_FAILURE,
                    "The canonical manuscript main claim differs from the frozen theorem.",
                )
                fidelity_verified = False
    if not bibliography:
        add(
            "empty_bibliography",
            ManuscriptFindingSeverity.REPAIRABLE,
            "references.bib contains no BibTeX entries.",
        )
    if missing:
        add(
            "missing_bibliography_keys",
            ManuscriptFindingSeverity.REPAIRABLE,
            "Cited keys missing from references.bib: " + ", ".join(missing),
        )
    for issue in bibliography_report.issues:
        add(
            issue.code,
            ManuscriptFindingSeverity.REPAIRABLE,
            issue.message,
            "Complete or correct the cited bibliography record.",
        )
    for warning in bibliography_report.warnings:
        add(
            warning.code,
            ManuscriptFindingSeverity.PUBLICATION_WARNING,
            warning.message,
        )
    if "\\begin{document}" not in paper_tex or "\\end{document}" not in paper_tex:
        add(
            "incomplete_latex_document",
            ManuscriptFindingSeverity.REPAIRABLE,
            "paper.tex is not a complete LaTeX document.",
        )
    blocking_or_repairable = [
        finding
        for finding in findings
        if finding.severity
        in {ManuscriptFindingSeverity.HARD_FAILURE, ManuscriptFindingSeverity.REPAIRABLE}
    ]
    return RelatedWorkValidation(
        passed=not blocking_or_repairable,
        has_related_work_section=has_section,
        has_introduction_section=has_introduction,
        has_ai_usage_statement=ai_usage_report.has_statement_section,
        ai_usage_disclosure_verified=ai_usage_report.passed,
        matek_repository_citation_key=ai_usage_report.repository_citation_key,
        matek_whitepaper_citation_key=ai_usage_report.whitepaper_citation_key,
        matek_technical_report_citation_key=ai_usage_report.technical_report_citation_key,
        matek_whitepaper_citation_pending=ai_usage_report.matek_whitepaper_citation_pending,
        introduction_word_count=introduction_words,
        related_work_word_count=related_words,
        introduction_coverage_verified=coverage_verified,
        frozen_claim_fidelity_verified=fidelity_verified,
        cited_keys=cited,
        bibliography_keys=bibliography,
        missing_bibliography_keys=missing,
        issues=[finding.message for finding in findings],
        findings=findings,
    )


def _audit_gate_issues(
    audit: BibliographyAudit,
    validation: RelatedWorkValidation,
    paper_tex: str,
    references_bib: str,
    verified_identifiers: Collection[str] = (),
) -> list[str]:
    issues = list(audit.blocking_issues)
    parsed_entries, _ = parse_bibtex(references_bib)
    bibliography_identifiers = {
        entry.key: set().union(
            *(
                source_identifiers(entry.fields.get(field, ""))
                for field in ("doi", "eprint", "isbn", "url", "mrnumber")
            )
        )
        for entry in parsed_entries
    }
    audited = {entry.citation_key: entry for entry in audit.entries}
    for key in validation.bibliography_keys:
        entry = audited.get(key)
        if entry is None:
            issues.append(f"Bibliography entry {key!r} was not independently audited.")
        elif not entry.fully_verified:
            issues.append(f"Bibliography entry {key!r} is not fully verified.")
        else:
            stable_identifiers = bibliography_identifiers.get(key, set())
            if not stable_identifiers:
                issues.append(
                    f"Bibliography entry {key!r} has no quality stable identifier or HTTPS URL."
                )
            if stable_identifiers and not stable_identifiers.intersection(verified_identifiers):
                issues.append(f"Bibliography entry {key!r} was not independently resolved.")
            if any(key not in evidence.source_ids for evidence in entry.authoritative_evidence):
                issues.append(f"Bibliography evidence for {key!r} is not linked to its source ID.")
    for check in audit.claim_checks:
        if not check.supported:
            issues.append(f"Related-work characterization is unsupported: {check.claim}")
            continue
        if not check.claim.strip():
            issues.append("A related-work characterization audit has no claim text.")
        claim_keys = {key.strip() for key in check.citation_keys if key.strip()}
        if not claim_keys:
            issues.append(f"Related-work characterization has no cited source key: {check.claim}")
            continue
        unknown = claim_keys - set(audited)
        if unknown:
            issues.append(
                "Related-work characterization cites unaudited keys: " + ", ".join(sorted(unknown))
            )
        if not check.evidence:
            issues.append(
                f"Related-work characterization lacks authoritative evidence: {check.claim}"
            )
            continue
        valid_evidence = [
            evidence for evidence in check.evidence if isinstance(evidence, SourceEvidenceClaim)
        ]
        if len(valid_evidence) != len(check.evidence):
            issues.append(f"Related-work characterization has malformed evidence: {check.claim}")
        linked_ids = set().union(*(set(evidence.source_ids) for evidence in valid_evidence))
        if not claim_keys.issubset(linked_ids):
            issues.append(
                f"Related-work evidence is not linked to every cited source: {check.claim}"
            )
    if not audit.claim_checks:
        issues.append("No substantive related-work characterization was independently checked.")
    if audit.status != BibliographyStatus.VERIFIED:
        issues.append(f"Bibliography verifier status is {audit.status.value}.")
    deterministic = validate_bibliography(
        paper_tex,
        references_bib,
        audit.model_dump(mode="json"),
    )
    issues.extend(issue.message for issue in deterministic.issues)
    return list(dict.fromkeys(issues))


async def _verify_bibliography_identifiers(
    references_bib: str,
    *,
    provider_identifiers: Collection[str],
    verifier: IdentifierVerifier | None,
) -> SourceVerificationReport:
    records: list[SourceVerificationRecord] = []
    warnings: list[str] = []
    parsed_entries, _ = parse_bibtex(references_bib)
    for entry in parsed_entries:
        identifiers = set().union(
            *(
                source_identifiers(entry.fields.get(field, ""))
                for field in ("doi", "eprint", "isbn", "url", "mrnumber")
            )
        )
        provider_records = provider_verification_records(identifiers, provider_identifiers)
        records.extend(provider_records)
        unresolved = identifiers - {record.identifier for record in provider_records}
        if unresolved and verifier is not None:
            deterministic = await verifier.verify(
                unresolved,
                expected_title=entry.fields.get("title"),
            )
            records.extend(deterministic.records)
            warnings.extend(deterministic.warnings)
        elif unresolved:
            records.extend(
                SourceVerificationRecord(
                    identifier=identifier,
                    status=SourceVerificationStatus.UNAVAILABLE,
                    detail="deterministic source verifier is not configured",
                )
                for identifier in sorted(unresolved)
            )
    return SourceVerificationReport(records=records, warnings=list(dict.fromkeys(warnings)))


def _bibliography_markdown(audit: BibliographyAudit, issues: list[str]) -> str:
    lines = ["# Bibliography Audit", "", f"Status: **{audit.status.value}**", ""]
    lines.append("## Entries")
    lines.append("")
    for entry in audit.entries:
        marker = "verified" if entry.fully_verified else "blocking"
        lines.append(f"- `{entry.citation_key}` — {entry.status.value} ({marker})")
    lines.extend(["", "## Blocking issues", ""])
    if issues:
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def _bibliography_findings(
    audit: BibliographyAudit | None,
    gate_issues: Collection[str],
) -> list[ManuscriptFinding]:
    nonexistent_keys = {
        entry.citation_key
        for entry in (audit.entries if audit is not None else [])
        if entry.status is BibliographyEntryStatus.NONEXISTENT
    }
    findings: list[ManuscriptFinding] = []
    for issue in gate_issues:
        audit_unavailable = issue.startswith(
            ("Independent bibliography audit failed:", "Bibliography audit is unavailable")
        )
        fabricated = any(repr(key) in issue or key in issue for key in nonexistent_keys)
        findings.append(
            ManuscriptFinding(
                code=(
                    "fabricated_citation"
                    if fabricated
                    else (
                        "bibliography_audit_unavailable"
                        if audit_unavailable
                        else "bibliography_verification_failed"
                    )
                ),
                severity=(
                    ManuscriptFindingSeverity.HARD_FAILURE
                    if fabricated
                    else ManuscriptFindingSeverity.REPAIRABLE
                ),
                message=issue,
                repair=(
                    "Remove the nonexistent citation and any dependent claim."
                    if fabricated
                    else (
                        "Retry the independent bibliography audit from this preserved draft."
                        if audit_unavailable
                        else "Correct the entry or qualify/remove the dependent literature claim."
                    )
                ),
            )
        )
    return findings


async def generate_manuscript(
    *,
    client: ModelClient,
    backend: ExecutionBackend,
    research_result: ResearchResult,
    claim_contract: dict[str, Any],
    source_ledger: list[dict[str, Any]],
    knowledge_graph_context: dict[str, object] | None = None,
    manuscript_dir: Path,
    writer_settings: ModelSettings | None = None,
    verifier_settings: ModelSettings | None = None,
    maximum_correction_cycles: int = 2,
    latex_command: tuple[str, ...] = (
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "paper.tex",
    ),
    latex_timeout_seconds: int = 600,
    manuscript_prompt_path: Path | None = None,
    bibliography_prompt_path: Path | None = None,
    resume_from: ManuscriptResult | None = None,
    source_verifier: IdentifierVerifier | None = None,
) -> ManuscriptResult:
    """Write and independently verify a manuscript after the accepted research gate.

    ``manuscript_dir`` is the final stage directory; all contracted manuscript artifacts are
    written directly beneath it.  A rejected/partial research result raises ``StageGateError``
    before any model or filesystem mutation.  The verifier receives a fresh model call on
    every cycle, and LaTeX success is classified from the injected deterministic backend.
    """

    if (
        research_result.outcome != ResearchOutcome.ACCEPTED
        or not research_result.accepted_for_manuscript
        or research_result.candidate is None
        or research_result.acceptance_gate is None
    ):
        raise StageGateError("Manuscript generation requires an accepted research gate.")
    if maximum_correction_cycles < 0:
        raise StageValidationError("maximum_correction_cycles must be nonnegative.")
    if sha256_json(research_result.candidate) != research_result.acceptance_gate.candidate_sha256:
        raise StageGateError("The accepted research package no longer matches its gate hash.")
    claim_contract_sha256 = sha256_text(
        json.dumps(claim_contract, sort_keys=True, ensure_ascii=False)
    )
    if claim_contract_sha256 != research_result.acceptance_gate.claim_contract_sha256:
        raise StageGateError("The manuscript claim contract does not match the research gate.")
    if resume_from is not None:
        if resume_from.has_terminating_failure:
            raise StageGateError("A manuscript with a terminating trust failure cannot be resumed.")
        if (
            resume_from.research_gate.candidate_sha256
            != research_result.acceptance_gate.candidate_sha256
        ):
            raise StageGateError("Persisted manuscript does not match the accepted proof gate.")
        if maximum_correction_cycles <= resume_from.correction_cycles:
            raise StageValidationError(
                "A bibliography resume must add at least one correction cycle."
            )
    if not latex_command:
        raise StageValidationError("latex_command must contain an executable.")
    hardened_latex_command = harden_latex_command(latex_command)

    writer_model = writer_settings or ModelSettings(reasoning_effort="xhigh", web_search=True)
    verifier_model = verifier_settings or ModelSettings(reasoning_effort="xhigh", web_search=True)

    destination = ensure_stage_directory(manuscript_dir)
    drafts_dir = ensure_stage_directory(destination / "drafts")
    writer_prompt = manuscript_prompt_path or project_resource("prompts/manuscript_writer.md")
    verifier_prompt = bibliography_prompt_path or project_resource(
        "prompts/bibliography_verifier.md"
    )
    try:
        writer_instructions = writer_prompt.read_text(encoding="utf-8")
        verifier_instructions = verifier_prompt.read_text(encoding="utf-8")
    except OSError as exc:
        raise StageValidationError(f"Cannot read a manuscript-stage prompt: {exc}") from exc

    response_ids: list[str] = []
    model_calls = 0
    correction_cycles = resume_from.correction_cycles if resume_from is not None else 0
    artifact_paths: dict[str, Path] = {}
    accepted_candidate_sha256 = research_result.acceptance_gate.candidate_sha256
    accepted_exact_theorem = research_result.candidate.exact_theorem
    frozen_input = {
        "frozen_candidate_package": research_result.candidate.model_dump(mode="json"),
        "frozen_candidate_sha256": accepted_candidate_sha256,
        "claim_contract": claim_contract,
        "frozen_claim_contract_sha256": claim_contract_sha256,
        "independent_research_audits": {
            name: audit.model_dump(mode="json") for name, audit in research_result.audits.items()
        },
        "source_ledger": source_ledger,
        "knowledge_graph_context": knowledge_graph_context,
        "mandatory_structured_content": {
            "introduction_coverage": (
                "Provide structured claims for related work, difference from prior work, and "
                "the precise advance, plus the same inline citation keys. The prose may be a "
                "semantically equivalent paraphrase."
            ),
            "frozen_claim_fidelity": (
                "Copy both supplied SHA-256 values and the exact accepted theorem; the "
                "manuscript main claim must be identical and exact_match must be true."
            ),
            "statement_of_ai_usage": (
                "Include an explicit Statement of AI Usage saying verbatim that 'The MATEK "
                "system with GPT 5.6 was used' and cite the canonical MATEK GitHub repository. "
                "Cite the local MATEK technical report when available. If no canonical "
                "whitepaper arXiv ID is supplied, leave that citation pending and never invent "
                "metadata or insert a deliberate LaTeX build failure."
            ),
        },
    }

    def validate_draft(candidate_draft: ManuscriptDraft) -> RelatedWorkValidation:
        return validate_related_work(
            candidate_draft.paper_tex,
            candidate_draft.references_bib,
            introduction_coverage=candidate_draft.introduction_coverage,
            frozen_claim_fidelity=candidate_draft.frozen_claim_fidelity,
            expected_candidate_sha256=accepted_candidate_sha256,
            expected_claim_contract_sha256=claim_contract_sha256,
            expected_exact_theorem=accepted_exact_theorem,
        )

    async def write_draft(
        *,
        previous: ManuscriptDraft | None = None,
        correction_plan: list[str] | None = None,
    ) -> ManuscriptDraft:
        nonlocal model_calls
        payload: dict[str, Any] = dict(frozen_input)
        if previous is not None:
            payload["previous_manuscript"] = previous.model_dump(mode="json")
            payload["mandatory_validation_corrections"] = correction_plan or []
            payload["instruction"] = (
                "Regenerate from the same frozen proof. Repair the listed presentation, source, "
                "metadata, or LaTeX findings without changing the mathematical claim or proof."
            )
        model_calls += 1
        result = await client.generate_structured(
            ModelRequest(
                instructions=writer_instructions,
                input_text=json.dumps(payload, ensure_ascii=False),
                settings=writer_model,
            ),
            ManuscriptDraft,
        )
        response_ids.append(result.response_id)
        return result.parsed

    async def verify_draft(
        draft: ManuscriptDraft,
    ) -> tuple[BibliographyAudit, tuple[Mapping[str, Any], ...]]:
        nonlocal model_calls
        model_calls += 1
        result = await client.generate_structured(
            ModelRequest(
                instructions=verifier_instructions,
                input_text=json.dumps(
                    {
                        "paper_tex": draft.paper_tex,
                        "references_bib": draft.references_bib,
                        "claim_contract": claim_contract,
                        "web_search_required": True,
                        "verification_requirement": (
                            "Independently check every entry and every substantive related-work "
                            "characterization against authoritative public sources. Evidence "
                            "must include the matching DOI, arXiv/ISBN/MR identifier, or a "
                            "non-placeholder authoritative HTTPS URL. Also verify that the "
                            "Statement of AI Usage names the MATEK system with GPT 5.6 and cites "
                            "the canonical MATEK GitHub record. A missing canonical whitepaper "
                            "arXiv ID is pending metadata, not permission to invent a record."
                        ),
                    },
                    ensure_ascii=False,
                ),
                settings=verifier_model,
            ),
            BibliographyAudit,
        )
        response_ids.append(result.response_id)
        return result.parsed, result.tool_metadata

    if resume_from is None:
        draft = await write_draft()
    else:
        previous_repairs = [
            finding.repair or finding.message
            for finding in resume_from.findings
            if finding.severity is ManuscriptFindingSeverity.REPAIRABLE
        ]
        if resume_from.bibliography_audit is not None:
            previous_repairs = [
                *resume_from.bibliography_audit.correction_plan,
                *previous_repairs,
            ]
        correction_cycles += 1
        draft = await write_draft(
            previous=resume_from.draft,
            correction_plan=previous_repairs,
        )
    cycle_records: list[
        tuple[
            int,
            ManuscriptDraft,
            RelatedWorkValidation,
            BibliographyAudit | None,
            list[str],
            LatexBuildResult | None,
            list[ManuscriptFinding],
            tuple[Mapping[str, Any], ...],
            SourceVerificationReport,
        ]
    ] = []
    terminal_findings: list[ManuscriptFinding] = []

    def publish_draft(candidate_draft: ManuscriptDraft) -> None:
        artifact_paths["paper_tex"] = atomic_write_text(
            destination / "paper.tex", candidate_draft.paper_tex
        )
        artifact_paths["references_bib"] = atomic_write_text(
            destination / "references.bib", candidate_draft.references_bib
        )
        artifact_paths["claims"] = atomic_write_json(
            destination / "claims.json",
            [claim.model_dump(mode="json") for claim in candidate_draft.claims],
        )
        artifact_paths["proof_dependency_graph"] = atomic_write_json(
            destination / "proof_dependency_graph.json",
            [
                dependency.model_dump(mode="json")
                for dependency in candidate_draft.proof_dependency_graph
            ],
        )
        artifact_paths["introduction_coverage"] = atomic_write_json(
            destination / "introduction_coverage.json", candidate_draft.introduction_coverage
        )
        artifact_paths["frozen_claim_fidelity"] = atomic_write_json(
            destination / "frozen_claim_fidelity.json", candidate_draft.frozen_claim_fidelity
        )

    async def build_draft(
        *, cycle: int, cycle_dir: Path, safe_to_execute: bool
    ) -> LatexBuildResult | None:
        if not safe_to_execute:
            return None
        pdf_path = destination / "paper.pdf"
        if pdf_path.exists():
            backup = cycle_dir / "preexisting-paper.pdf"
            suffix = 1
            while backup.exists():
                backup = cycle_dir / f"preexisting-paper-{suffix}.pdf"
                suffix += 1
            pdf_path.replace(backup)
            artifact_paths[f"draft_{cycle}_preexisting_pdf"] = backup
        command = CommandRequest(
            argv=hardened_latex_command,
            cwd=destination,
            timeout_seconds=latex_timeout_seconds,
        )
        try:
            command_result: CommandResult = await backend.run(command)
            log_text = (
                "$ "
                + " ".join(command_result.argv)
                + "\n\n[stdout]\n"
                + command_result.stdout
                + "\n\n[stderr]\n"
                + command_result.stderr
            )
            classified = classify_latex_result(command_result, log_text)
            diagnostics = [issue.message for issue in classified.issues]
            exit_code: int | None = command_result.exit_code
        except Exception as exc:
            log_text = f"LaTeX backend failed before returning a command result: {exc}\n"
            diagnostics = [str(exc)]
            exit_code = None
        cycle_log = atomic_write_text(cycle_dir / "build.log", log_text)
        artifact_paths[f"draft_{cycle}_build_log"] = cycle_log
        artifact_paths["build_log"] = atomic_write_text(destination / "build.log", log_text)
        pdf_exists = pdf_path.is_file() and pdf_path.stat().st_size > 0
        if not pdf_exists:
            diagnostics.append("LaTeX did not produce a nonempty paper.pdf.")
        build_passed = exit_code == 0 and not diagnostics and pdf_exists
        cycle_pdf: Path | None = None
        if pdf_exists:
            cycle_pdf = cycle_dir / "paper.pdf"
            shutil.copy2(pdf_path, cycle_pdf)
            artifact_paths[f"draft_{cycle}_paper_pdf"] = cycle_pdf
        return LatexBuildResult(
            passed=build_passed,
            argv=list(hardened_latex_command),
            exit_code=exit_code,
            diagnostics=list(dict.fromkeys(diagnostics)),
            pdf_path=cycle_pdf,
        )

    while True:
        cycle_dir = ensure_stage_directory(drafts_dir / str(correction_cycles))
        artifact_paths[f"draft_{correction_cycles}_paper_tex"] = atomic_write_text(
            cycle_dir / "paper.tex", draft.paper_tex
        )
        artifact_paths[f"draft_{correction_cycles}_references_bib"] = atomic_write_text(
            cycle_dir / "references.bib", draft.references_bib
        )
        publish_draft(draft)
        related = validate_draft(draft)

        audit: BibliographyAudit | None = None
        provider_metadata: tuple[Mapping[str, Any], ...] = ()
        source_verification = SourceVerificationReport(records=[], warnings=[])
        gate_issues: list[str] = []
        if not verifier_model.web_search:
            gate_issues.append(
                "Bibliography audit is unavailable because verifier web search is disabled."
            )
        else:
            try:
                audit, provider_metadata = await verify_draft(draft)
                provider_identifiers = tool_metadata_source_identifiers(provider_metadata)
                try:
                    source_verification = await _verify_bibliography_identifiers(
                        draft.references_bib,
                        provider_identifiers=provider_identifiers,
                        verifier=source_verifier,
                    )
                except Exception as exc:
                    source_verification = SourceVerificationReport(
                        records=[],
                        warnings=[f"Deterministic identifier verification failed: {exc}"],
                    )
                gate_issues = _audit_gate_issues(
                    audit,
                    related,
                    draft.paper_tex,
                    draft.references_bib,
                    source_verification.verified_identifiers,
                )
            except Exception as exc:
                gate_issues.append(f"Independent bibliography audit failed: {exc}")

        if audit is not None:
            artifact_paths[f"draft_{correction_cycles}_bibliography_audit"] = atomic_write_json(
                cycle_dir / "bibliography_audit.json", audit
            )
        artifact_paths[f"draft_{correction_cycles}_bibliography_provider_metadata"] = (
            atomic_write_json(
                cycle_dir / "bibliography_provider_metadata.json",
                [dict(item) for item in provider_metadata],
            )
        )
        artifact_paths[f"draft_{correction_cycles}_source_verification"] = atomic_write_json(
            cycle_dir / "source_verification.json", source_verification
        )

        unsafe = any(
            finding.code in {"unsafe_tex_output", "deliberate_latex_failure"}
            for finding in related.findings
        )
        latex_build = await build_draft(
            cycle=correction_cycles,
            cycle_dir=cycle_dir,
            safe_to_execute=not unsafe,
        )
        cycle_findings = [*related.findings, *_bibliography_findings(audit, gate_issues)]
        if latex_build is not None and not latex_build.passed:
            cycle_findings.extend(
                ManuscriptFinding(
                    code="latex_build_failed",
                    severity=ManuscriptFindingSeverity.REPAIRABLE,
                    message=diagnostic,
                    repair="Repair the LaTeX source and rebuild in the next revision round.",
                )
                for diagnostic in latex_build.diagnostics
            )
        validation = ManuscriptDraftValidation(
            cycle=correction_cycles,
            related_work=related,
            bibliography_audit=audit,
            bibliography_gate_issues=gate_issues,
            latex_build=latex_build,
            findings=cycle_findings,
        )
        artifact_paths[f"draft_{correction_cycles}_validation"] = atomic_write_json(
            cycle_dir / "validation.json", validation
        )
        cycle_records.append(
            (
                correction_cycles,
                draft,
                related,
                audit,
                gate_issues,
                latex_build,
                cycle_findings,
                provider_metadata,
                source_verification,
            )
        )

        hard_failure = any(
            finding.severity is ManuscriptFindingSeverity.HARD_FAILURE for finding in cycle_findings
        )
        repairable = [
            finding
            for finding in cycle_findings
            if finding.severity is ManuscriptFindingSeverity.REPAIRABLE
        ]
        writer_repairable = [
            finding for finding in repairable if finding.code != "bibliography_audit_unavailable"
        ]
        needs_revision = bool(writer_repairable) or latex_build is None or not latex_build.passed
        if hard_failure or not needs_revision or correction_cycles >= maximum_correction_cycles:
            break
        correction_cycles += 1
        correction_plan = [finding.repair or finding.message for finding in writer_repairable]
        if audit is not None:
            correction_plan = [*audit.correction_plan, *correction_plan]
        try:
            draft = await write_draft(previous=draft, correction_plan=correction_plan)
        except Exception as exc:
            terminal_findings.append(
                ManuscriptFinding(
                    code="manuscript_revision_failed",
                    severity=ManuscriptFindingSeverity.REPAIRABLE,
                    message=f"Manuscript revision round {correction_cycles} failed: {exc}",
                    repair="Resume from the preserved best draft and retry this revision.",
                )
            )
            break

    def record_score(
        record: tuple[
            int,
            ManuscriptDraft,
            RelatedWorkValidation,
            BibliographyAudit | None,
            list[str],
            LatexBuildResult | None,
            list[ManuscriptFinding],
            tuple[Mapping[str, Any], ...],
            SourceVerificationReport,
        ],
    ) -> tuple[int, int, int, int, int]:
        cycle, _, _, _, gate, build, findings, _, _ = record
        hard_count = sum(
            finding.severity is ManuscriptFindingSeverity.HARD_FAILURE for finding in findings
        )
        repair_count = sum(
            finding.severity is ManuscriptFindingSeverity.REPAIRABLE for finding in findings
        )
        return (
            hard_count,
            repair_count + len(gate),
            int(build is None or not build.passed),
            len(findings),
            -cycle,
        )

    selected = min(cycle_records, key=record_score)
    (
        selected_cycle,
        draft,
        related,
        audit,
        gate_issues,
        latex_build,
        selected_findings,
        provider_metadata,
        source_verification,
    ) = selected
    findings = [*selected_findings, *terminal_findings]
    repairs_exhausted = (
        any(finding.severity is ManuscriptFindingSeverity.REPAIRABLE for finding in findings)
        and correction_cycles >= maximum_correction_cycles
    )
    selected_has_hard_failure = any(
        finding.severity is ManuscriptFindingSeverity.HARD_FAILURE for finding in findings
    )
    latex_failed = latex_build is None or not latex_build.passed
    if latex_failed and repairs_exhausted and not selected_has_hard_failure:
        findings.append(
            ManuscriptFinding(
                code="irreparable_latex",
                severity=ManuscriptFindingSeverity.HARD_FAILURE,
                message=(
                    "LaTeX remained unavailable or invalid after all configured repair attempts."
                ),
                repair="Repair the preserved draft and resume manuscript validation.",
            )
        )

    publish_draft(draft)
    selected_cycle_dir = drafts_dir / str(selected_cycle)
    selected_pdf = selected_cycle_dir / "paper.pdf"
    root_pdf = destination / "paper.pdf"
    if selected_pdf.is_file() and selected_pdf.stat().st_size > 0:
        shutil.copy2(selected_pdf, root_pdf)
        artifact_paths["paper_pdf"] = root_pdf
        if latex_build is not None:
            latex_build = latex_build.model_copy(update={"pdf_path": root_pdf})
    elif root_pdf.exists():
        preserved = destination / "unselected-paper.pdf"
        root_pdf.replace(preserved)
        artifact_paths["unselected_paper_pdf"] = preserved

    if audit is not None:
        artifact_paths["bibliography_audit"] = atomic_write_json(
            destination / "bibliography_audit.json", audit
        )
        artifact_paths["bibliography_audit_markdown"] = atomic_write_text(
            destination / "bibliography_audit.md", _bibliography_markdown(audit, gate_issues)
        )
    artifact_paths["bibliography_provider_metadata"] = atomic_write_json(
        destination / "bibliography_provider_metadata.json",
        [dict(item) for item in provider_metadata],
    )
    artifact_paths["bibliography_source_verification"] = atomic_write_json(
        destination / "bibliography_source_verification.json", source_verification
    )
    artifact_paths["validation"] = atomic_write_json(
        destination / "validation.json",
        ManuscriptDraftValidation(
            cycle=selected_cycle,
            related_work=related,
            bibliography_audit=audit,
            bibliography_gate_issues=gate_issues,
            latex_build=latex_build,
            findings=findings,
        ),
    )

    bibliography_verified = audit is not None and not gate_issues
    hard_failure = any(
        finding.severity is ManuscriptFindingSeverity.HARD_FAILURE for finding in findings
    )
    warning_codes = {
        finding.code
        for finding in findings
        if finding.severity is ManuscriptFindingSeverity.PUBLICATION_WARNING
    }
    repairable_codes = {
        finding.code
        for finding in findings
        if finding.severity is ManuscriptFindingSeverity.REPAIRABLE
    }
    if hard_failure:
        manuscript_status = ManuscriptStatus.PUBLICATION_BLOCKED
        outcome = ManuscriptOutcome.PUBLICATION_BLOCKED
        publication_status = (
            PublicationStatus.BLOCKED_LATEX
            if "irreparable_latex" in {finding.code for finding in findings}
            else PublicationStatus.BLOCKED_INTEGRITY
        )
    elif repairable_codes or warning_codes or not bibliography_verified:
        manuscript_status = ManuscriptStatus.DRAFT_WITH_WARNINGS
        outcome = ManuscriptOutcome.DRAFT_WITH_WARNINGS
        if latex_failed:
            publication_status = PublicationStatus.BLOCKED_LATEX
        elif gate_issues or not bibliography_verified:
            publication_status = PublicationStatus.BLOCKED_BIBLIOGRAPHY
        elif "matek_whitepaper_citation_pending" in warning_codes:
            publication_status = PublicationStatus.BLOCKED_METADATA
        else:
            publication_status = PublicationStatus.BLOCKED_CONTENT
    else:
        manuscript_status = ManuscriptStatus.PUBLICATION_READY
        outcome = ManuscriptOutcome.COMPILED
        publication_status = PublicationStatus.READY

    result = ManuscriptResult(
        outcome=outcome,
        draft=draft,
        bibliography_audit=audit,
        bibliography_verified=bibliography_verified,
        related_work=related,
        latex_build=latex_build,
        correction_cycles=correction_cycles,
        research_gate=research_result.acceptance_gate,
        manuscript_status=manuscript_status,
        publication_status=publication_status,
        findings=findings,
        selected_draft_cycle=selected_cycle,
        revision_rounds_exhausted=repairs_exhausted,
        artifacts=build_artifact_manifest(artifact_paths),
        calls=CallManifest(model_calls=model_calls, response_ids=response_ids),
    )
    atomic_write_json(destination / "result.json", result)
    return result


async def resume_manuscript_bibliography(
    *,
    client: ModelClient,
    backend: ExecutionBackend,
    previous_result: ManuscriptResult,
    research_result: ResearchResult,
    claim_contract: dict[str, Any],
    source_ledger: list[dict[str, Any]],
    knowledge_graph_context: dict[str, object] | None = None,
    manuscript_dir: Path,
    maximum_additional_correction_cycles: int = 1,
    writer_settings: ModelSettings | None = None,
    verifier_settings: ModelSettings | None = None,
    latex_command: tuple[str, ...] = (
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "paper.tex",
    ),
    latex_timeout_seconds: int = 600,
    manuscript_prompt_path: Path | None = None,
    bibliography_prompt_path: Path | None = None,
    source_verifier: IdentifierVerifier | None = None,
) -> ManuscriptResult:
    """Resume manuscript correction from a preserved non-terminating draft.

    The completed initial manuscript-writer call is never repeated.  The persisted draft and
    findings seed one correction call, followed by fresh independent bibliography verification
    and deterministic LaTeX compilation. ``manuscript_dir`` is the final stage directory used by
    the original call.
    """

    if maximum_additional_correction_cycles < 1:
        raise StageValidationError("maximum_additional_correction_cycles must be at least one.")
    total_limit = previous_result.correction_cycles + maximum_additional_correction_cycles
    return await generate_manuscript(
        client=client,
        backend=backend,
        research_result=research_result,
        claim_contract=claim_contract,
        source_ledger=source_ledger,
        knowledge_graph_context=knowledge_graph_context,
        manuscript_dir=manuscript_dir,
        writer_settings=writer_settings,
        verifier_settings=verifier_settings,
        maximum_correction_cycles=total_limit,
        latex_command=latex_command,
        latex_timeout_seconds=latex_timeout_seconds,
        manuscript_prompt_path=manuscript_prompt_path,
        bibliography_prompt_path=bibliography_prompt_path,
        resume_from=previous_result,
        source_verifier=source_verifier,
    )
