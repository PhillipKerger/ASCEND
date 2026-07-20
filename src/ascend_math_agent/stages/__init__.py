"""Dependency-injected workflow stage services and their gate result models."""

from .compile_prompt import (
    LiteratureStatus,
    PromptCompilationResult,
    PromptCompilationStatus,
    compile_prompt,
)
from .lean import LeanPipelineResult, run_lean_pipeline
from .manuscript import (
    ManuscriptResult,
    generate_manuscript,
    resume_manuscript_bibliography,
)
from .research import ResearchResult, run_adaptive_research

__all__ = [
    "LeanPipelineResult",
    "LiteratureStatus",
    "ManuscriptResult",
    "PromptCompilationResult",
    "PromptCompilationStatus",
    "ResearchResult",
    "compile_prompt",
    "generate_manuscript",
    "resume_manuscript_bibliography",
    "run_adaptive_research",
    "run_lean_pipeline",
]
