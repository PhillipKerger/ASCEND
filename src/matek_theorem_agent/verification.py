from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .execution.base import CommandResult


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    message: str
    path: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path is not None:
            result["path"] = self.path
        if self.line is not None:
            result["line"] = self.line
        return result


class LatexClassification(StrEnum):
    SUCCESS = "success"
    COMMAND_FAILED = "command_failed"
    COMPILATION_ERROR = "compilation_error"
    UNDEFINED_CITATIONS = "undefined_citations"
    UNDEFINED_REFERENCES = "undefined_references"
    BIBLIOGRAPHY_ERROR = "bibliography_error"
    OUTPUT_TRUNCATED = "output_truncated"


@dataclass(frozen=True)
class LatexVerificationReport:
    passed: bool
    classification: LatexClassification
    issues: tuple[VerificationIssue, ...]
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "classification": self.classification.value,
            "exit_code": self.exit_code,
            "issues": [issue.to_dict() for issue in self.issues],
        }


_LATEX_PATTERNS: tuple[tuple[str, LatexClassification, re.Pattern[str]], ...] = (
    (
        "undefined_citations",
        LatexClassification.UNDEFINED_CITATIONS,
        re.compile(r"(?is)(?:Citation [`']?[^\n]*? undefined|There were undefined citations)"),
    ),
    (
        "undefined_references",
        LatexClassification.UNDEFINED_REFERENCES,
        re.compile(r"(?is)(?:Reference [`']?[^\n]*? undefined|There were undefined references)"),
    ),
    (
        "bibliography_error",
        LatexClassification.BIBLIOGRAPHY_ERROR,
        re.compile(
            r"(?is)(?:I couldn't open database file|No file [^\n]*\.bbl|"
            r"Please \(re\)run Biber|Please rerun BibTeX|"
            r"Database file .* not found|empty bibliography)"
        ),
    ),
    (
        "compilation_error",
        LatexClassification.COMPILATION_ERROR,
        re.compile(
            r"(?im)^(?:! (?:LaTeX|Package|Class).*Error|! Undefined control sequence|"
            r"! Emergency stop|.*Fatal error occurred)"
        ),
    ),
)


def classify_latex_result(
    result: CommandResult, build_log: str | None = None
) -> LatexVerificationReport:
    """Classify a deterministic LaTeX command without trusting a generated claim."""

    combined = "\n".join(part for part in (result.stdout, result.stderr, build_log) if part)
    issues: list[VerificationIssue] = []
    matched: list[LatexClassification] = []
    if result.timed_out:
        issues.append(VerificationIssue("command_timed_out", "LaTeX command timed out"))
    if result.stdout_truncated or result.stderr_truncated:
        matched.append(LatexClassification.OUTPUT_TRUNCATED)
        issues.append(
            VerificationIssue(
                "output_truncated",
                "LaTeX output exceeded the configured capture bound",
            )
        )
    for code, classification, pattern in _LATEX_PATTERNS:
        if pattern.search(combined):
            matched.append(classification)
            issues.append(VerificationIssue(code, code.replace("_", " ").capitalize()))
    if result.exit_code != 0 or result.timed_out:
        matched.append(LatexClassification.COMMAND_FAILED)
        issues.append(
            VerificationIssue(
                "nonzero_exit",
                f"LaTeX command exited with code {result.exit_code}",
            )
        )

    priority = (
        LatexClassification.COMMAND_FAILED,
        LatexClassification.OUTPUT_TRUNCATED,
        LatexClassification.COMPILATION_ERROR,
        LatexClassification.UNDEFINED_CITATIONS,
        LatexClassification.UNDEFINED_REFERENCES,
        LatexClassification.BIBLIOGRAPHY_ERROR,
    )
    classification = next(
        (candidate for candidate in priority if candidate in matched),
        LatexClassification.SUCCESS,
    )
    return LatexVerificationReport(
        passed=not issues,
        classification=classification,
        issues=tuple(_deduplicate_issues(issues)),
        exit_code=result.exit_code,
    )


# A compatibility name that reads naturally at call sites.
classify_latex_command = classify_latex_result


@dataclass(frozen=True)
class BibEntry:
    key: str
    entry_type: str
    fields: Mapping[str, str]


@dataclass(frozen=True)
class BibliographyValidationReport:
    passed: bool
    cited_keys: tuple[str, ...]
    bibliography_keys: tuple[str, ...]
    uncited_keys: tuple[str, ...]
    issues: tuple[VerificationIssue, ...]
    warnings: tuple[VerificationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "cited_keys": list(self.cited_keys),
            "bibliography_keys": list(self.bibliography_keys),
            "uncited_keys": list(self.uncited_keys),
            "issues": [issue.to_dict() for issue in self.issues],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


@dataclass(frozen=True)
class AIUsageValidationReport:
    """Deterministic validation of MATEK attribution in a generated manuscript."""

    passed: bool
    has_statement_section: bool
    discloses_matek_with_gpt_5_6: bool
    citation_keys: tuple[str, ...]
    repository_citation_key: str | None
    whitepaper_citation_key: str | None
    issues: tuple[VerificationIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "has_statement_section": self.has_statement_section,
            "discloses_matek_with_gpt_5_6": self.discloses_matek_with_gpt_5_6,
            "citation_keys": list(self.citation_keys),
            "repository_citation_key": self.repository_citation_key,
            "whitepaper_citation_key": self.whitepaper_citation_key,
            "issues": [issue.to_dict() for issue in self.issues],
        }


_CITATION_PATTERN = re.compile(
    r"\\(?:[A-Za-z]*cite[A-Za-z]*|nocite)\*?"
    r"(?:\s*\[[^\]]*\]){0,2}\s*\{([^{}]*)\}"
)
_AI_USAGE_SECTION_PATTERN = re.compile(
    r"\\(?:sub)*section\*?\s*\{\s*Statement\s+of\s+AI\s+Usage\s*\}",
    re.IGNORECASE,
)
_SECTION_PATTERN = re.compile(r"\\(?:sub)*section\*?\s*\{", re.IGNORECASE)
_MATEK_GPT_5_6_DISCLOSURE = re.compile(
    r"\bMATEK system with GPT 5[.]6 was used\b",
    re.IGNORECASE,
)
_GITHUB_REPOSITORY_URL = re.compile(
    r"https://(?:www[.])?github[.]com/([^/\s{},]+)/([^/\s{},]+)",
    re.IGNORECASE,
)
_ARXIV_IDENTIFIER = re.compile(
    r"(?i)(?:\barxiv\s*:\s*|https?://(?:www[.])?arxiv[.]org/(?:abs|pdf)/)?"
    r"((?:\d{4}[.]\d{4,5}|[a-z-]+(?:[.][a-z-]+)?/\d{7})(?:v\d+)?)"
)
_CITATION_PLACEHOLDER = re.compile(
    r"(?i)(?:<[^>]+>|\b(?:OWNER|ARXIV_ID|REPOSITORY_URL|GITHUB_URL)\b)"
)
_VENUE_FIELDS = frozenset(
    {
        "journal",
        "journaltitle",
        "booktitle",
        "eventtitle",
        "publisher",
        "school",
        "institution",
        "organization",
        "howpublished",
        "note",
        "eprint",
        "url",
    }
)
_IDENTIFIER_FIELDS = frozenset({"doi", "eprint", "isbn", "url", "mrnumber"})


def extract_latex_citations(tex_source: str) -> tuple[str, ...]:
    source = _strip_latex_comments(tex_source)
    keys: set[str] = set()
    for match in _CITATION_PATTERN.finditer(source):
        for raw_key in match.group(1).split(","):
            key = raw_key.strip()
            if key and key != "*":
                keys.add(key)
    return tuple(sorted(keys))


def validate_matek_ai_usage(
    tex_source: str,
    bib_source: str,
) -> AIUsageValidationReport:
    """Require MATEK/GPT 5.6 disclosure plus distinct software and preprint citations.

    This validator deliberately checks citation *roles*, not hard-coded project identifiers.
    Canonical repository ownership and the whitepaper's arXiv identifier are release metadata,
    and the bibliography stage remains responsible for independently verifying them. This
    function rejects placeholder metadata and ensures the disclosure cites a MATEK-titled
    GitHub record and a separate MATEK-titled arXiv record.
    """

    source = _strip_latex_comments(tex_source)
    section_match = _AI_USAGE_SECTION_PATTERN.search(source)
    section_body = ""
    if section_match is not None:
        next_section = _SECTION_PATTERN.search(source, section_match.end())
        end = next_section.start() if next_section is not None else len(source)
        section_body = source[section_match.end() : end]

    normalized_body = _normalize_latex_prose(section_body)
    disclosure_found = bool(_MATEK_GPT_5_6_DISCLOSURE.search(normalized_body))
    citation_keys = extract_latex_citations(section_body)
    entries, parse_errors = parse_bibtex(bib_source)
    entry_map = {entry.key: entry for entry in entries}
    issues = [VerificationIssue("invalid_bibtex", error) for error in parse_errors]

    if section_match is None:
        issues.append(
            VerificationIssue(
                "missing_ai_usage_statement",
                "The manuscript has no explicit Statement of AI Usage section.",
            )
        )
    elif not disclosure_found:
        issues.append(
            VerificationIssue(
                "incomplete_ai_usage_statement",
                "The Statement of AI Usage must state that the MATEK system with GPT 5.6 was used.",
            )
        )

    repository_candidates: list[str] = []
    whitepaper_candidates: list[str] = []
    for key in citation_keys:
        entry = entry_map.get(key)
        if entry is None:
            continue
        title = _normalize_latex_prose(entry.fields.get("title", ""))
        if "matek" not in title.casefold():
            continue
        field_text = " ".join(entry.fields.values())
        if _CITATION_PLACEHOLDER.search(field_text):
            continue
        repository_match = _GITHUB_REPOSITORY_URL.search(field_text)
        if repository_match is not None and not _placeholder_github_path(repository_match):
            repository_candidates.append(key)
        if _entry_has_arxiv_identifier(entry):
            whitepaper_candidates.append(key)

    repository_key = repository_candidates[0] if repository_candidates else None
    whitepaper_key = next(
        (key for key in whitepaper_candidates if key != repository_key),
        whitepaper_candidates[0] if whitepaper_candidates else None,
    )

    if repository_key is None:
        issues.append(
            VerificationIssue(
                "missing_matek_repository_citation",
                "The Statement of AI Usage must cite the canonical MATEK GitHub repository.",
            )
        )
    if whitepaper_key is None:
        issues.append(
            VerificationIssue(
                "missing_matek_whitepaper_citation",
                "The Statement of AI Usage must cite the MATEK whitepaper arXiv preprint.",
            )
        )
    if repository_key is not None and repository_key == whitepaper_key:
        issues.append(
            VerificationIssue(
                "matek_citations_not_distinct",
                "The MATEK software repository and whitepaper must be separate citations.",
            )
        )

    deduplicated = tuple(_deduplicate_issues(issues))
    return AIUsageValidationReport(
        passed=not deduplicated,
        has_statement_section=section_match is not None,
        discloses_matek_with_gpt_5_6=disclosure_found,
        citation_keys=citation_keys,
        repository_citation_key=repository_key,
        whitepaper_citation_key=whitepaper_key,
        issues=deduplicated,
    )


def _normalize_latex_prose(value: str) -> str:
    normalized = re.sub(r"\\(?:emph|textbf|textit|texttt|mbox)\s*\{([^{}]*)\}", r"\1", value)
    normalized = normalized.replace("~", " ").replace("--", " ").replace("-", " ")
    normalized = normalized.replace("{", " ").replace("}", " ")
    return " ".join(normalized.split())


def _placeholder_github_path(match: re.Match[str]) -> bool:
    placeholder_parts = {
        "owner",
        "org",
        "organization",
        "repo",
        "repository",
        "username",
        "your-org",
        "your-repo",
    }
    return any(part.casefold().strip("./") in placeholder_parts for part in match.groups())


def _entry_has_arxiv_identifier(entry: BibEntry) -> bool:
    eprint = entry.fields.get("eprint", "").strip()
    if eprint and _ARXIV_IDENTIFIER.fullmatch(eprint):
        return True
    return any(
        _ARXIV_IDENTIFIER.search(entry.fields.get(field, "")) is not None
        for field in ("url", "howpublished", "note")
    )


def parse_bibtex(bib_source: str) -> tuple[tuple[BibEntry, ...], tuple[str, ...]]:
    """Parse enough BibTeX structure for deterministic consistency checks.

    The parser is deliberately conservative: malformed or duplicate entries are
    returned as errors instead of being guessed into validity.
    """

    bib_source = _strip_bibtex_comments(bib_source)
    entries: list[BibEntry] = []
    errors: list[str] = []
    seen: set[str] = set()
    position = 0
    length = len(bib_source)
    while position < length:
        at = bib_source.find("@", position)
        if at < 0:
            break
        type_match = re.match(r"@\s*([A-Za-z]+)\s*([({])", bib_source[at:])
        if type_match is None:
            errors.append(f"malformed BibTeX entry near character {at}")
            position = at + 1
            continue
        entry_type = type_match.group(1).lower()
        opening = type_match.group(2)
        opening_index = at + type_match.end() - 1
        closing_index = _find_balanced_end(bib_source, opening_index, opening)
        if closing_index is None:
            errors.append(f"unterminated BibTeX entry near character {at}")
            break
        position = closing_index + 1
        if entry_type in {"comment", "preamble", "string"}:
            continue

        body = bib_source[opening_index + 1 : closing_index]
        key_part, fields_part = _split_first_top_level_comma(body)
        key = key_part.strip()
        if not key:
            errors.append(f"BibTeX {entry_type} entry has an empty key")
            continue
        if key in seen:
            errors.append(f"duplicate BibTeX key: {key}")
            continue
        seen.add(key)
        try:
            fields = _parse_bib_fields(fields_part)
        except ValueError as exc:
            errors.append(f"BibTeX entry {key}: {exc}")
            fields = {}
        entries.append(BibEntry(key=key, entry_type=entry_type, fields=fields))
    return tuple(entries), tuple(errors)


def validate_bibliography(
    tex_source: str,
    bib_source: str,
    audit: Mapping[str, Any] | None = None,
) -> BibliographyValidationReport:
    cited = extract_latex_citations(tex_source)
    entries, parse_errors = parse_bibtex(bib_source)
    entry_map = {entry.key: entry for entry in entries}
    issues = [VerificationIssue("invalid_bibtex", error) for error in parse_errors]
    warnings: list[VerificationIssue] = []

    for key in cited:
        if key not in entry_map:
            issues.append(
                VerificationIssue("missing_bibliography_entry", f"Citation {key!r} is missing")
            )
    for entry in entries:
        if entry.key not in cited:
            continue
        fields = entry.fields
        for required in ("title",):
            if not _field_present(fields, required):
                issues.append(
                    VerificationIssue(
                        "missing_bibliography_metadata",
                        f"Bibliography entry {entry.key!r} lacks {required}",
                    )
                )
        if not (_field_present(fields, "author") or _field_present(fields, "editor")):
            issues.append(
                VerificationIssue(
                    "missing_bibliography_metadata",
                    f"Bibliography entry {entry.key!r} lacks author/editor",
                )
            )
        if not (_field_present(fields, "year") or _field_present(fields, "date")):
            issues.append(
                VerificationIssue(
                    "missing_bibliography_metadata",
                    f"Bibliography entry {entry.key!r} lacks year/date",
                )
            )
        elif not _valid_bibliography_date(fields):
            issues.append(
                VerificationIssue(
                    "invalid_bibliography_metadata",
                    f"Bibliography entry {entry.key!r} has an invalid year/date",
                )
            )
        if not any(_field_present(fields, name) for name in _VENUE_FIELDS):
            issues.append(
                VerificationIssue(
                    "missing_bibliography_metadata",
                    f"Bibliography entry {entry.key!r} lacks venue/publication status",
                )
            )
        if not any(_field_present(fields, name) for name in _IDENTIFIER_FIELDS):
            warnings.append(
                VerificationIssue(
                    "missing_stable_identifier",
                    f"Bibliography entry {entry.key!r} has no stable identifier/URL",
                )
            )

    if audit is not None:
        issues.extend(_validate_bibliography_audit(cited, audit))

    bib_keys = tuple(sorted(entry_map))
    uncited = tuple(sorted(set(bib_keys) - set(cited)))
    return BibliographyValidationReport(
        passed=not issues,
        cited_keys=cited,
        bibliography_keys=bib_keys,
        uncited_keys=uncited,
        issues=tuple(_deduplicate_issues(issues)),
        warnings=tuple(_deduplicate_issues(warnings)),
    )


def validate_bibliography_files(
    tex_path: Path,
    bib_path: Path,
    audit_path: Path | None = None,
) -> BibliographyValidationReport:
    tex = tex_path.read_text(encoding="utf-8")
    bib = bib_path.read_text(encoding="utf-8")
    audit: Mapping[str, Any] | None = None
    if audit_path is not None:
        value = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("bibliography audit must be a JSON object")
        audit = value
    return validate_bibliography(tex, bib, audit)


# Compatibility name used by the deterministic ``verify`` entry point.
verify_bibliography_consistency = validate_bibliography_files


def _validate_bibliography_audit(
    cited_keys: Sequence[str], audit: Mapping[str, Any]
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    if audit.get("status") != "verified":
        issues.append(
            VerificationIssue(
                "bibliography_audit_not_verified",
                "Bibliography audit status is not verified",
            )
        )
    blocking = audit.get("blocking_issues")
    if not isinstance(blocking, list):
        issues.append(
            VerificationIssue("invalid_bibliography_audit", "blocking_issues must be an array")
        )
    elif blocking:
        issues.append(
            VerificationIssue(
                "bibliography_audit_blocked",
                "Bibliography audit contains blocking issues",
            )
        )

    raw_entries = audit.get("entries")
    if not isinstance(raw_entries, list):
        issues.append(VerificationIssue("invalid_bibliography_audit", "entries must be an array"))
        return issues
    statuses: dict[str, str] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            issues.append(
                VerificationIssue("invalid_bibliography_audit", "audit entry must be an object")
            )
            continue
        key = next(
            (
                raw_entry.get(name)
                for name in ("key", "citation_key", "cite_key", "id")
                if isinstance(raw_entry.get(name), str)
            ),
            None,
        )
        status = raw_entry.get("status")
        if isinstance(key, str) and isinstance(status, str):
            statuses[key] = status
    for key in cited_keys:
        if statuses.get(key) != "verified":
            issues.append(
                VerificationIssue(
                    "citation_not_independently_verified",
                    f"Citation {key!r} is not independently verified",
                )
            )

    claim_checks = audit.get("claim_checks")
    if not isinstance(claim_checks, list):
        issues.append(
            VerificationIssue("invalid_bibliography_audit", "claim_checks must be an array")
        )
    else:
        for index, claim in enumerate(claim_checks):
            if not isinstance(claim, Mapping):
                issues.append(
                    VerificationIssue(
                        "invalid_bibliography_audit",
                        f"claim check {index} must be an object",
                    )
                )
                continue
            status = claim.get("status")
            supported = claim.get("supported")
            if status not in {"verified", "supported", "pass"} and supported is not True:
                issues.append(
                    VerificationIssue(
                        "related_work_claim_not_verified",
                        f"Related-work claim check {index} is not verified",
                    )
                )
    return issues


def _strip_latex_comments(source: str) -> str:
    lines: list[str] = []
    for line in source.splitlines(keepends=True):
        cut = len(line)
        for index, character in enumerate(line):
            if character != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                cut = index
                break
        suffix = "\n" if line.endswith("\n") and cut < len(line) else ""
        lines.append(line[:cut] + suffix)
    return "".join(lines)


def _strip_bibtex_comments(source: str) -> str:
    """Remove unescaped percent comments outside quoted field content."""

    lines: list[str] = []
    for line in source.splitlines(keepends=True):
        in_quote = False
        escaped = False
        cut = len(line)
        for index, character in enumerate(line):
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == '"':
                in_quote = not in_quote
                continue
            if not in_quote and character == "%":
                cut = index
                break
        suffix = "\n" if line.endswith("\n") and cut < len(line) else ""
        lines.append(line[:cut] + suffix)
    return "".join(lines)


def _find_balanced_end(source: str, opening_index: int, opening: str) -> int | None:
    closing = "}" if opening == "{" else ")"
    depth = 0
    in_quote = False
    escaped = False
    for index in range(opening_index, len(source)):
        character = source[index]
        if in_quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_quote = False
            continue
        if character == '"':
            in_quote = True
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_first_top_level_comma(value: str) -> tuple[str, str]:
    depths = {"{": 0, "(": 0, "[": 0}
    closings = {"}": "{", ")": "(", "]": "["}
    in_quote = False
    escaped = False
    for index, character in enumerate(value):
        if in_quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_quote = False
            continue
        if character == '"':
            in_quote = True
        elif character in depths:
            depths[character] += 1
        elif character in closings:
            depths[closings[character]] -= 1
        elif character == "," and not any(depths.values()):
            return value[:index], value[index + 1 :]
    return value, ""


def _parse_bib_fields(source: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    position = 0
    while position < len(source):
        while position < len(source) and (source[position].isspace() or source[position] == ","):
            position += 1
        if position >= len(source):
            break
        name_match = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=", source[position:])
        if name_match is None:
            raise ValueError(f"malformed field near {source[position : position + 30]!r}")
        name = name_match.group(1).lower()
        position += name_match.end()
        value_start = position
        braces = 0
        parentheses = 0
        in_quote = False
        escaped = False
        while position < len(source):
            character = source[position]
            if in_quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_quote = False
            elif character == '"':
                in_quote = True
            elif character == "{":
                braces += 1
            elif character == "}":
                braces -= 1
                if braces < 0:
                    raise ValueError(f"unbalanced braces in field {name}")
            elif character == "(":
                parentheses += 1
            elif character == ")":
                parentheses -= 1
            elif character == "," and braces == 0 and parentheses == 0:
                break
            position += 1
        raw_value = source[value_start:position].strip()
        if not raw_value:
            raise ValueError(f"field {name} is empty")
        if name in fields:
            raise ValueError(f"field {name} is duplicated")
        fields[name] = _normalize_bib_value(raw_value)
        if position < len(source) and source[position] == ",":
            position += 1
    return fields


def _normalize_bib_value(value: str) -> str:
    normalized = value.strip()
    while len(normalized) >= 2 and (
        (normalized[0] == "{" and normalized[-1] == "}")
        or (normalized[0] == '"' and normalized[-1] == '"')
    ):
        normalized = normalized[1:-1].strip()
    return " ".join(normalized.split())


def _field_present(fields: Mapping[str, str], name: str) -> bool:
    return bool(fields.get(name, "").strip())


def _valid_bibliography_date(fields: Mapping[str, str]) -> bool:
    year = fields.get("year", "").strip()
    date = fields.get("date", "").strip()
    if year and re.fullmatch(r"\d{4}[a-z]?", year, re.IGNORECASE):
        return True
    return bool(date and re.match(r"\d{4}(?:-\d{2}(?:-\d{2})?)?\Z", date))


@dataclass(frozen=True)
class LeanFinding:
    code: str
    path: str
    line: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "line": self.line,
            "message": self.message,
        }


@dataclass(frozen=True)
class LeanScanReport:
    passed: bool
    root: str
    files: tuple[str, ...]
    findings: tuple[LeanFinding, ...]
    file_hashes: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "root": self.root,
            "files": list(self.files),
            "findings": [finding.to_dict() for finding in self.findings],
            "file_hashes": dict(sorted(self.file_hashes.items())),
        }


_LEAN_CODE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "placeholder_sorry",
        re.compile(r"(?<![\w.'])\bsorry\b|\bsorryAx\b"),
        "prohibited sorry placeholder",
    ),
    (
        "placeholder_admit",
        re.compile(r"(?<![\w.'])\badmit\b"),
        "prohibited admit placeholder",
    ),
    (
        "placeholder_by_question",
        re.compile(r"(?<![\w?])\bby\?(?![\w?])"),
        "prohibited by? placeholder",
    ),
    (
        "placeholder_tactic_question",
        re.compile(r"(?<![\w.])(?:exact|apply|refine|simp|aesop)\?(?![\w?])"),
        "prohibited tactic suggestion placeholder",
    ),
    (
        "placeholder_hole",
        re.compile(r"(?<![\w?])\?_(?![\w?])"),
        "prohibited metavariable hole",
    ),
    (
        "unsafe_declaration",
        re.compile(r"(?<![\w.'])\bunsafe\b"),
        "unsafe code is not eligible for deterministic verification",
    ),
    (
        "implementation_override",
        re.compile(r"\bimplemented_by\b"),
        "implementation override may bypass the audited declaration",
    ),
    (
        "compile_time_execution",
        re.compile(
            r"(?m)^\s*(?:run_cmd\b|#\s*(?:eval|reduce)\b|(?:builtin_)?initialize\b)"
            r"|(?<![\w.'])\brun_tac\b"
        ),
        "compile-time execution is prohibited in generated proof sources",
    ),
    (
        "custom_elaborator",
        re.compile(
            r"(?m)^\s*(?:(?:local|scoped)\s+)?"
            r"(?:syntax|macro|macro_rules|elab|elab_rules)\b"
        ),
        "custom syntax, macros, and elaborators are prohibited in generated proof sources",
    ),
    (
        "foreign_implementation",
        re.compile(r"@\[\s*(?:extern|implemented_by)\b"),
        "foreign or replacement implementations are prohibited in generated proof sources",
    ),
    (
        "compile_time_file_read",
        re.compile(r"(?<![\w.'])\binclude_(?:str|bytes)\b"),
        "compile-time file inclusion is prohibited in generated proof sources",
    ),
)
_LEAN_AXIOM_PATTERN = re.compile(
    r"(?m)^\s*(?:private\s+)?(?:axiom|axioms|constant|constants)\s+"
    r"([A-Za-z_\u0080-\uffff][\w.'\u0080-\uffff]*)"
)
_LEAN_OPAQUE_PATTERN = re.compile(
    r"(?m)^\s*(?:private\s+)?opaque\s+"
    r"([A-Za-z_\u0080-\uffff][\w.'\u0080-\uffff]*)"
)
_TODO_PATTERN = re.compile(r"(?i)\bTODO\b")


def _read_regular_bytes_no_follow(
    path: Path,
    *,
    expected: os.stat_result | None = None,
) -> bytes:
    """Read one regular file while refusing a final-component symlink or swap."""

    entry = expected if expected is not None else os.lstat(path)
    if not stat.S_ISREG(entry.st_mode):
        raise OSError(f"not a regular file: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError(f"not a regular file: {path}")
        if (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino):
            raise OSError(f"file changed while being opened: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def scan_lean_tree(root: Path, target_theorem: str | None = None) -> LeanScanReport:
    """Recursively scan every run-local Lean file without following symlinks."""

    canonical_root = _canonical_lean_root(root)
    files: list[str] = []
    findings: list[LeanFinding] = []
    file_hashes: dict[str, str] = {}

    for current, directories, names in os.walk(canonical_root, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for directory in sorted(directories):
            directory_path = current_path / directory
            relative = directory_path.relative_to(canonical_root).as_posix()
            try:
                entry = os.lstat(directory_path)
            except OSError as exc:
                findings.append(
                    LeanFinding(
                        "unreadable_tree_entry",
                        relative,
                        1,
                        f"cannot inspect run-local entry: {type(exc).__name__}",
                    )
                )
                continue
            if stat.S_ISLNK(entry.st_mode):
                findings.append(
                    LeanFinding(
                        "symlink_not_scanned",
                        relative,
                        1,
                        "run-local Lean scan refuses symlinked directories",
                    )
                )
            elif not stat.S_ISDIR(entry.st_mode):
                findings.append(
                    LeanFinding(
                        "non_regular_tree_entry",
                        relative,
                        1,
                        "run-local Lean scan refuses non-directory tree entries",
                    )
                )
            else:
                safe_directories.append(directory)
        directories[:] = safe_directories

        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(canonical_root).as_posix()
            try:
                entry = os.lstat(path)
            except OSError as exc:
                findings.append(
                    LeanFinding(
                        "unreadable_tree_entry",
                        relative,
                        1,
                        f"cannot inspect run-local entry: {type(exc).__name__}",
                    )
                )
                continue
            if stat.S_ISLNK(entry.st_mode):
                findings.append(
                    LeanFinding(
                        "symlink_not_scanned",
                        relative,
                        1,
                        "run-local Lean scan refuses symlinked files",
                    )
                )
                continue
            if not stat.S_ISREG(entry.st_mode):
                findings.append(
                    LeanFinding(
                        "non_regular_tree_entry",
                        relative,
                        1,
                        "run-local Lean scan refuses non-regular files",
                    )
                )
                continue
            if not name.endswith(".lean"):
                continue
            files.append(relative)
            try:
                payload = _read_regular_bytes_no_follow(path, expected=entry)
                source = payload.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                findings.append(
                    LeanFinding(
                        "unreadable_lean_file",
                        relative,
                        1,
                        f"cannot read UTF-8 Lean source: {type(exc).__name__}",
                    )
                )
                continue
            file_hashes[relative] = hashlib.sha256(payload).hexdigest()
            code = _strip_lean_comments(source, preserve_strings=False)
            for code_name, pattern, message in _LEAN_CODE_PATTERNS:
                for match in pattern.finditer(code):
                    findings.append(
                        LeanFinding(
                            code_name,
                            relative,
                            _line_number(source, match.start()),
                            message,
                        )
                    )
            for match in _TODO_PATTERN.finditer(code):
                findings.append(
                    LeanFinding(
                        "placeholder_todo",
                        relative,
                        _line_number(source, match.start()),
                        "unresolved TODO marker",
                    )
                )
            for match in _LEAN_AXIOM_PATTERN.finditer(code):
                declaration_name = match.group(1)
                detail = "new axiom/constant declaration is prohibited"
                if target_theorem is not None and declaration_name == target_theorem:
                    detail = "target theorem was encoded as an axiom/constant"
                findings.append(
                    LeanFinding(
                        "suspicious_axiom_declaration",
                        relative,
                        _line_number(source, match.start()),
                        detail,
                    )
                )
            for match in _LEAN_OPAQUE_PATTERN.finditer(code):
                declaration_name = match.group(1)
                detail = (
                    f"opaque {declaration_name} declaration is prohibited in generated "
                    "proof sources"
                )
                if target_theorem is not None and declaration_name == target_theorem:
                    detail = "target theorem was encoded as an opaque declaration"
                findings.append(
                    LeanFinding(
                        "opaque_declaration",
                        relative,
                        _line_number(source, match.start()),
                        detail,
                    )
                )

    if not files:
        findings.append(
            LeanFinding(
                "no_lean_files",
                ".",
                1,
                "run-local Lean tree contains no .lean files",
            )
        )
    findings.sort(key=lambda item: (item.path, item.line, item.code))
    return LeanScanReport(
        passed=not findings,
        root=str(canonical_root),
        files=tuple(sorted(files)),
        findings=tuple(findings),
        file_hashes=file_hashes,
    )


# Compatibility names for focused checks and older call sites.
scan_lean_files = scan_lean_tree
scan_lean_placeholders = scan_lean_tree


@dataclass(frozen=True)
class TheoremStatement:
    name: str
    declaration_kind: str
    source: str
    canonical: str
    sha256: str
    line: int


class TheoremStatementError(ValueError):
    pass


_THEOREM_DECLARATION = re.compile(
    r"(?m)(?<![\w.])(?:(?:private|protected|noncomputable)\s+)*"
    r"(theorem|lemma)\s+"
    r"([A-Za-z_\u0080-\uffff][\w.'\u0080-\uffff]*)"
)


def extract_theorem_statements(source: str) -> tuple[TheoremStatement, ...]:
    cleaned = _strip_lean_comments(source, preserve_strings=True)
    statements: list[TheoremStatement] = []
    for match in _THEOREM_DECLARATION.finditer(cleaned):
        end = _find_statement_end(cleaned, match.end())
        if end is None:
            continue
        raw_statement = source[match.start() : end]
        canonical = canonicalize_lean_statement(raw_statement)
        statements.append(
            TheoremStatement(
                name=match.group(2),
                declaration_kind=match.group(1),
                source=raw_statement,
                canonical=canonical,
                sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                line=_line_number(source, match.start()),
            )
        )
    return tuple(statements)


def extract_theorem_statement(source: str, theorem_name: str) -> str:
    matches = [
        statement.source
        for statement in extract_theorem_statements(source)
        if statement.name == theorem_name
    ]
    if not matches:
        raise TheoremStatementError(f"theorem {theorem_name!r} was not found")
    if len(matches) != 1:
        raise TheoremStatementError(f"theorem {theorem_name!r} is declared more than once")
    return matches[0]


def canonicalize_lean_statement(statement: str) -> str:
    """Canonicalize lexical trivia while preserving every meaningful Lean token."""

    cleaned = unicodedata.normalize("NFC", _strip_lean_comments(statement, preserve_strings=True))
    tokens = _lean_tokens(cleaned)
    if not tokens:
        raise TheoremStatementError("theorem statement is empty")
    return "\x1f".join(tokens)


def canonical_theorem_hash(source_or_path: str | Path, theorem_name: str | None = None) -> str:
    source = (
        _read_regular_bytes_no_follow(source_or_path).decode("utf-8")
        if isinstance(source_or_path, Path)
        else source_or_path
    )
    statements = extract_theorem_statements(source)
    if theorem_name is not None:
        matching = [item for item in statements if item.name == theorem_name]
        if not matching:
            raise TheoremStatementError(f"theorem {theorem_name!r} was not found")
        if len(matching) != 1:
            raise TheoremStatementError(f"theorem {theorem_name!r} is declared more than once")
        return matching[0].sha256
    if len(statements) == 1:
        return statements[0].sha256
    if len(statements) > 1:
        raise TheoremStatementError("multiple theorem statements found; theorem_name is required")
    canonical = canonicalize_lean_statement(source)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


theorem_statement_hash = canonical_theorem_hash


def _find_statement_end(source: str, start: int) -> int | None:
    parentheses = 0
    brackets = 0
    braces = 0
    in_string = False
    escaped = False
    pending_top_level_let = False
    index = start
    while index < len(source) - 1:
        character = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
        elif parentheses == brackets == braces == 0 and (character.isalpha() or character == "_"):
            word_start = index
            index += 1
            while index < len(source) and (source[index].isalnum() or source[index] in "_'"):
                index += 1
            if source[word_start:index] == "let":
                pending_top_level_let = True
            continue
        elif character == "(":
            parentheses += 1
        elif character == ")":
            parentheses = max(0, parentheses - 1)
        elif character == "[":
            brackets += 1
        elif character == "]":
            brackets = max(0, brackets - 1)
        elif character == "{":
            braces += 1
        elif character == "}":
            braces = max(0, braces - 1)
        elif (
            character == ":" and source[index + 1] == "=" and parentheses == brackets == braces == 0
        ):
            if pending_top_level_let:
                pending_top_level_let = False
                index += 2
                continue
            return index
        index += 1
    return None


def _lean_tokens(source: str) -> tuple[str, ...]:
    tokens: list[str] = []
    index = 0
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if character == '"':
            start = index
            index += 1
            escaped = False
            while index < len(source):
                current = source[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
            tokens.append(source[start:index])
            continue
        category = unicodedata.category(character)
        if character == "_" or character == "'" or category[0] in {"L", "N", "M"}:
            start = index
            index += 1
            while index < len(source):
                current = source[index]
                current_category = unicodedata.category(current)
                if not (current == "_" or current == "'" or current_category[0] in {"L", "N", "M"}):
                    break
                index += 1
            tokens.append(source[start:index])
            continue
        if character in "()[]{};,":
            tokens.append(character)
            index += 1
            continue
        start = index
        index += 1
        while index < len(source):
            current = source[index]
            if current.isspace() or current in '"()[]{};,':
                break
            current_category = unicodedata.category(current)
            if current == "_" or current == "'" or current_category[0] in {"L", "N", "M"}:
                break
            index += 1
        tokens.append(source[start:index])
    return tuple(tokens)


def _strip_lean_comments(source: str, *, preserve_strings: bool) -> str:
    """Replace comments (and optionally strings) with spaces, preserving offsets."""

    result = list(source)
    index = 0
    length = len(source)
    while index < length:
        if source.startswith("--", index):
            cursor = index
            while cursor < length and source[cursor] != "\n":
                result[cursor] = " "
                cursor += 1
            index = cursor
            continue
        if source.startswith("/-", index):
            depth = 1
            result[index] = result[index + 1] = " "
            cursor = index + 2
            while cursor < length and depth:
                if source.startswith("/-", cursor):
                    depth += 1
                    result[cursor] = result[cursor + 1] = " "
                    cursor += 2
                elif source.startswith("-/", cursor):
                    depth -= 1
                    result[cursor] = result[cursor + 1] = " "
                    cursor += 2
                else:
                    if source[cursor] != "\n":
                        result[cursor] = " "
                    cursor += 1
            index = cursor
            continue
        if source[index] == '"':
            cursor = index + 1
            escaped = False
            if not preserve_strings:
                result[index] = " "
            while cursor < length:
                character = source[cursor]
                if not preserve_strings and character != "\n":
                    result[cursor] = " "
                cursor += 1
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    break
            index = cursor
            continue
        index += 1
    return "".join(result)


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _canonical_lean_root(root: Path) -> Path:
    try:
        root_entry = os.lstat(root)
    except OSError as exc:
        raise ValueError(f"Lean root does not exist: {root}") from exc
    if stat.S_ISLNK(root_entry.st_mode):
        raise ValueError(f"Lean root must not be a symlink: {root}")
    if not stat.S_ISDIR(root_entry.st_mode):
        raise ValueError(f"Lean root is not a directory: {root}")
    try:
        canonical = root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Lean root does not exist: {root}") from exc
    if not canonical.is_dir():
        raise ValueError(f"Lean root is not a directory: {root}")
    if canonical == Path(canonical.anchor):
        raise ValueError("Lean root must not be a filesystem root")
    return canonical


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_AXIOM_LIST_PATTERN = re.compile(
    r"(?is)(?:depends\s+on\s+axioms|axioms\s+(?:used|required)|axioms)\s*:"
    r"\s*\[([^]]*)\]"
)
_NO_AXIOMS_PATTERN = re.compile(
    r"(?i)(?:does\s+not\s+depend\s+on\s+any\s+axioms|depends\s+on\s+no\s+axioms)"
)
_AXIOM_NAME_PATTERN = re.compile(r"[A-Za-z_\u0080-\uffff][\w.'\u0080-\uffff]*")
_NEVER_APPROVED_AXIOMS = frozenset({"sorryAx", "Lean.ofReduceBool"})


@dataclass(frozen=True)
class AxiomValidationReport:
    passed: bool
    output_recognized: bool
    used_axioms: tuple[str, ...]
    approved_axioms: tuple[str, ...]
    unapproved_axioms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "output_recognized": self.output_recognized,
            "used_axioms": list(self.used_axioms),
            "approved_axioms": list(self.approved_axioms),
            "unapproved_axioms": list(self.unapproved_axioms),
        }


def parse_print_axioms(output: str) -> frozenset[str]:
    """Parse Lean ``#print axioms`` output, returning the union of named axioms."""

    cleaned = _ANSI_ESCAPE.sub("", output)
    axioms: set[str] = set()
    for match in _AXIOM_LIST_PATTERN.finditer(cleaned):
        for candidate in _AXIOM_NAME_PATTERN.findall(match.group(1)):
            axioms.add(candidate)
    return frozenset(axioms)


def check_axiom_allowlist(output: str, approved_axioms: Iterable[str]) -> AxiomValidationReport:
    cleaned = _ANSI_ESCAPE.sub("", output)
    recognized = bool(_AXIOM_LIST_PATTERN.search(cleaned) or _NO_AXIOMS_PATTERN.search(cleaned))
    used = parse_print_axioms(cleaned)
    approved = frozenset(str(axiom).strip() for axiom in approved_axioms if str(axiom).strip())
    unapproved = (used - approved) | (used & _NEVER_APPROVED_AXIOMS)
    return AxiomValidationReport(
        passed=recognized and not unapproved,
        output_recognized=recognized,
        used_axioms=tuple(sorted(used)),
        approved_axioms=tuple(sorted(approved)),
        unapproved_axioms=tuple(sorted(unapproved)),
    )


validate_axiom_allowlist = check_axiom_allowlist


class LeanVerificationStatus(StrEnum):
    LEAN_FAILED = "LEAN_FAILED"
    LEAN_VERIFIED_WITH_APPROVED_AXIOMS = "LEAN_VERIFIED_WITH_APPROVED_AXIOMS"
    LEAN_VERIFIED = "LEAN_VERIFIED"


@dataclass(frozen=True)
class VerificationCertificate:
    passed: bool
    status: LeanVerificationStatus
    lean_root: str
    approved_statement_hash: str
    actual_statement_hash: str | None
    theorem_name: str | None
    command: tuple[str, ...]
    command_cwd: str
    build_exit_code: int
    checks: Mapping[str, bool]
    issues: tuple[VerificationIssue, ...]
    lean_files: tuple[str, ...]
    lean_file_hashes: Mapping[str, str]
    used_axioms: tuple[str, ...]
    approved_axioms: tuple[str, ...]
    unapproved_axioms: tuple[str, ...]
    build_stdout_sha256: str
    build_stderr_sha256: str
    axioms_output_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "passed": self.passed,
            "status": self.status.value,
            "lean_root": self.lean_root,
            "approved_statement_hash": self.approved_statement_hash,
            "actual_statement_hash": self.actual_statement_hash,
            "theorem_name": self.theorem_name,
            "command": list(self.command),
            "command_cwd": self.command_cwd,
            "build_exit_code": self.build_exit_code,
            "checks": dict(sorted(self.checks.items())),
            "issues": [issue.to_dict() for issue in self.issues],
            "lean_files": list(self.lean_files),
            "lean_file_hashes": dict(sorted(self.lean_file_hashes.items())),
            "used_axioms": list(self.used_axioms),
            "approved_axioms": list(self.approved_axioms),
            "unapproved_axioms": list(self.unapproved_axioms),
            "evidence_hashes": {
                "build_stdout_sha256": self.build_stdout_sha256,
                "build_stderr_sha256": self.build_stderr_sha256,
                "axioms_output_sha256": self.axioms_output_sha256,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def write(self, path: Path) -> None:
        _atomic_write_json(path, self.to_json())


BuildVerificationReport = VerificationCertificate


def verify_build(
    lean_root: Path,
    approved_statement_hash: str,
    command_result: CommandResult,
    axioms_output: str,
    approved_axioms: Iterable[str],
    *,
    theorem_name: str | None = None,
    statement_file: Path | None = None,
) -> VerificationCertificate:
    """Create a truthful deterministic Lean outcome and serializable certificate."""

    root = _canonical_lean_root(lean_root)
    normalized_approved_hash = approved_statement_hash.strip().lower()
    issues: list[VerificationIssue] = []
    hash_format_valid = bool(re.fullmatch(r"[0-9a-f]{64}", normalized_approved_hash))
    if not hash_format_valid:
        issues.append(
            VerificationIssue(
                "invalid_approved_statement_hash",
                "approved statement hash must be a lowercase or uppercase SHA-256 digest",
            )
        )

    scan = scan_lean_tree(root, target_theorem=theorem_name)
    for finding in scan.findings:
        issues.append(
            VerificationIssue(finding.code, finding.message, path=finding.path, line=finding.line)
        )

    statements, statement_issues = _collect_run_theorem_statements(
        root, statement_file=statement_file
    )
    issues.extend(statement_issues)
    relevant = (
        [statement for _, statement in statements if statement.name == theorem_name]
        if theorem_name is not None
        else [statement for _, statement in statements]
    )
    matching = [statement for statement in relevant if statement.sha256 == normalized_approved_hash]
    statement_matches = hash_format_valid and bool(matching)
    actual_statement_hash: str | None
    if matching:
        actual_statement_hash = matching[0].sha256
    elif len(relevant) == 1:
        actual_statement_hash = relevant[0].sha256
    else:
        actual_statement_hash = None
    if not statement_matches:
        label = f" named {theorem_name!r}" if theorem_name is not None else ""
        issues.append(
            VerificationIssue(
                "theorem_statement_mismatch",
                f"no run-local theorem{label} has the approved canonical hash",
            )
        )

    build_passed = command_result.exit_code == 0 and not command_result.timed_out
    if not build_passed:
        issues.append(
            VerificationIssue(
                "lean_build_failed",
                f"Lean build exited with code {command_result.exit_code}",
            )
        )
    output_complete = not (command_result.stdout_truncated or command_result.stderr_truncated)
    if not output_complete:
        issues.append(
            VerificationIssue(
                "lean_build_output_truncated",
                "Lean build diagnostics exceeded the configured capture bound",
            )
        )
    compiler_reported_sorry = bool(
        re.search(
            r"(?i)declaration\s+(?:uses|has)\s+['\"]?sorry",
            _command_output(command_result),
        )
    )
    if compiler_reported_sorry:
        issues.append(
            VerificationIssue(
                "compiler_reported_sorry",
                "Lean compiler diagnostics report a declaration using sorry",
            )
        )

    axiom_report = check_axiom_allowlist(axioms_output, approved_axioms)
    if not axiom_report.output_recognized:
        issues.append(
            VerificationIssue(
                "axiom_output_unrecognized",
                "no recognizable #print axioms result was captured",
            )
        )
    if axiom_report.unapproved_axioms:
        issues.append(
            VerificationIssue(
                "unapproved_axioms",
                "unapproved axioms: " + ", ".join(axiom_report.unapproved_axioms),
            )
        )

    checks = {
        "approved_hash_format_valid": hash_format_valid,
        "build_exit_zero": build_passed,
        "build_output_complete": output_complete,
        "compiler_reported_no_sorry": not compiler_reported_sorry,
        "lean_tree_scan_passed": scan.passed,
        "theorem_statement_unchanged": statement_matches,
        "axioms_output_recognized": axiom_report.output_recognized,
        "axioms_allowlisted": not axiom_report.unapproved_axioms,
    }
    passed = all(checks.values()) and not issues
    if not passed:
        status = LeanVerificationStatus.LEAN_FAILED
    elif axiom_report.used_axioms:
        status = LeanVerificationStatus.LEAN_VERIFIED_WITH_APPROVED_AXIOMS
    else:
        status = LeanVerificationStatus.LEAN_VERIFIED

    command = command_result.executed_argv or command_result.argv
    return VerificationCertificate(
        passed=passed,
        status=status,
        lean_root=str(root),
        approved_statement_hash=normalized_approved_hash,
        actual_statement_hash=actual_statement_hash,
        theorem_name=theorem_name,
        command=command,
        command_cwd=str(command_result.cwd),
        build_exit_code=command_result.exit_code,
        checks=checks,
        issues=tuple(_deduplicate_issues(issues)),
        lean_files=scan.files,
        lean_file_hashes=scan.file_hashes,
        used_axioms=axiom_report.used_axioms,
        approved_axioms=axiom_report.approved_axioms,
        unapproved_axioms=axiom_report.unapproved_axioms,
        build_stdout_sha256=_text_sha256(command_result.stdout),
        build_stderr_sha256=_text_sha256(command_result.stderr),
        axioms_output_sha256=_text_sha256(axioms_output),
    )


def write_verification_certificate(report: VerificationCertificate, path: Path) -> None:
    report.write(path)


def _collect_run_theorem_statements(
    root: Path, *, statement_file: Path | None
) -> tuple[list[tuple[str, TheoremStatement]], list[VerificationIssue]]:
    issues: list[VerificationIssue] = []
    statements: list[tuple[str, TheoremStatement]] = []
    if statement_file is not None:
        candidate = statement_file
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = Path(os.path.abspath(candidate))
        if not candidate.is_relative_to(root) or _path_contains_symlink(root, candidate):
            return [], [
                VerificationIssue(
                    "statement_file_outside_root",
                    "statement file must be a non-symlink under the run-local Lean root",
                )
            ]
        try:
            entry = os.lstat(candidate)
        except OSError:
            return [], [
                VerificationIssue(
                    "statement_file_missing", f"statement file does not exist: {candidate}"
                )
            ]
        if not stat.S_ISREG(entry.st_mode):
            return [], [
                VerificationIssue(
                    "statement_file_outside_root",
                    "statement file must be a non-symlink under the run-local Lean root",
                )
            ]
        paths = [candidate]
    else:
        paths = []
        for current, directories, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name for name in sorted(directories) if not (current_path / name).is_symlink()
            ]
            for name in sorted(names):
                path = current_path / name
                if not name.endswith(".lean"):
                    continue
                try:
                    entry = os.lstat(path)
                except OSError:
                    continue
                if stat.S_ISREG(entry.st_mode):
                    paths.append(path)
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            source = _read_regular_bytes_no_follow(path).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(
                VerificationIssue(
                    "unreadable_statement_file",
                    f"cannot read theorem statements: {type(exc).__name__}",
                    path=relative,
                )
            )
            continue
        for statement in extract_theorem_statements(source):
            statements.append((relative, statement))
    return statements, issues


def _path_contains_symlink(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
        except OSError:
            return False
    return False


def _command_output(result: CommandResult) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, content: str) -> None:
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    _validate_certificate_target(target)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_certificate_target(target)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_certificate_target(target: Path) -> None:
    try:
        entry = os.lstat(target)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"cannot inspect certificate path: {target}") from exc
    if stat.S_ISLNK(entry.st_mode):
        raise ValueError(f"certificate path must not be a symlink: {target}")
    if not stat.S_ISREG(entry.st_mode):
        raise ValueError(f"certificate path must be a regular file: {target}")


def _deduplicate_issues(
    issues: Iterable[VerificationIssue],
) -> list[VerificationIssue]:
    result: list[VerificationIssue] = []
    seen: set[tuple[str, str, str | None, int | None]] = set()
    for issue in issues:
        identity = (issue.code, issue.message, issue.path, issue.line)
        if identity not in seen:
            seen.add(identity)
            result.append(issue)
    return result
