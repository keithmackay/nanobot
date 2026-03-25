"""Large file handler for LCM Phase 5.

When a message contains content larger than LARGE_FILE_TOKEN_THRESHOLD tokens
(default 25k, ~100k characters), this module:
  1. Detects code blocks or whole-message pastes that exceed the threshold.
  2. Extracts them to ~/.nanobot/workspace/files/{session_id}/{file_id}.txt
  3. Replaces the large block in the message with a compact reference stub.
  4. Writes a brief structural summary (~200 tokens) alongside the file.

This prevents a single large paste from consuming the entire context window.
The stored file can be retrieved via `memory_describe("file:<file_id>")`.
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path


# Threshold: messages larger than this are candidates for extraction.
LARGE_FILE_TOKEN_THRESHOLD = 25_000  # tokens
# Rough chars-per-token (conservative estimate)
_CHARS_PER_TOKEN = 4

# Minimum size for a code block to be extracted independently (vs whole message)
_BLOCK_MIN_TOKENS = 10_000


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ExtractedFile:
    """Metadata for a file that was extracted from a message."""
    file_id: str
    path: Path
    summary_path: Path
    original_token_count: int
    language: str | None
    summary: str


@dataclass
class HandlerResult:
    """Result of processing a message through the large file handler."""
    processed_content: str       # Message content with large blocks replaced by stubs
    extracted: list[ExtractedFile]  # Files that were extracted


# ──────────────────────────────────────────────────────────────────────────────
# Structural summary generator
# ──────────────────────────────────────────────────────────────────────────────

def _build_summary(content: str, language: str | None, token_count: int) -> str:
    """
    Produce a brief structural summary (~200 tokens) of the extracted content.

    For code: counts top-level definitions, imports, and approximate line count.
    For plain text: counts lines and first/last few lines.
    """
    lines = content.splitlines()
    line_count = len(lines)

    if language in ("python", "py"):
        defs = [l.strip() for l in lines if re.match(r"^(def |class |async def )", l)]
        imports = [l.strip() for l in lines if re.match(r"^(import |from )", l)]
        summary_parts = [
            f"Python source — {line_count} lines, ~{token_count:,} tokens.",
            f"Imports ({len(imports)}): " + ", ".join(imports[:5]) + ("..." if len(imports) > 5 else ""),
            f"Definitions ({len(defs)}): " + ", ".join(defs[:8]) + ("..." if len(defs) > 8 else ""),
        ]
        return "\n".join(p for p in summary_parts if p)

    if language in ("javascript", "typescript", "js", "ts"):
        exports = [l.strip() for l in lines if re.match(r"^export ", l)]
        functions = [l.strip() for l in lines if re.match(r"^(function |const \w+ = |async function )", l)]
        summary_parts = [
            f"{language.capitalize()} source — {line_count} lines, ~{token_count:,} tokens.",
            f"Exports ({len(exports)}): " + ", ".join(exports[:5]) + ("..." if len(exports) > 5 else ""),
            f"Top-level functions ({len(functions)}): " + ", ".join(functions[:5]) + ("..." if len(functions) > 5 else ""),
        ]
        return "\n".join(p for p in summary_parts if p)

    # Generic: show first/last lines
    head = lines[:5]
    tail = lines[-3:] if line_count > 8 else []
    lang_label = f"{language} " if language else ""
    summary_parts = [
        f"{lang_label}content — {line_count} lines, ~{token_count:,} tokens.",
        "First lines: " + " | ".join(l.strip() for l in head if l.strip()),
    ]
    if tail:
        summary_parts.append("Last lines: " + " | ".join(l.strip() for l in tail if l.strip()))
    return "\n".join(summary_parts)


# ──────────────────────────────────────────────────────────────────────────────
# Extraction
# ──────────────────────────────────────────────────────────────────────────────

def _extract_block(
    content: str,
    language: str | None,
    session_id: str,
    files_dir: Path,
) -> ExtractedFile:
    """Write content to disk and return an ExtractedFile record."""
    # Stable file_id based on content hash (avoids duplicates)
    file_id = hashlib.sha1(content.encode()).hexdigest()[:12]
    lang_ext = language or "txt"
    file_path = files_dir / session_id / f"{file_id}.{lang_ext}"
    summary_path = files_dir / session_id / f"{file_id}.summary.txt"

    file_path.parent.mkdir(parents=True, exist_ok=True)

    token_count = _estimate_tokens(content)
    summary = _build_summary(content, language, token_count)

    file_path.write_text(content, encoding="utf-8")
    summary_path.write_text(summary, encoding="utf-8")

    return ExtractedFile(
        file_id=file_id,
        path=file_path,
        summary_path=summary_path,
        original_token_count=token_count,
        language=language,
        summary=summary,
    )


def _make_stub(ef: ExtractedFile) -> str:
    """Return a compact reference stub that replaces the extracted block."""
    lang_label = f"{ef.language} " if ef.language else ""
    return (
        f"[Extracted {lang_label}file: file:{ef.file_id} | "
        f"~{ef.original_token_count:,} tokens | "
        f"stored at {ef.path}]\n"
        f"Summary: {ef.summary}\n"
        f"Use memory_describe(\"file:{ef.file_id}\") to view full content."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

# Matches fenced code blocks: ```lang\n...\n```
_CODE_BLOCK_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_+-]*)?\n(?P<code>[\s\S]*?)```",
    re.MULTILINE,
)


def process_message(
    content: str,
    session_id: str,
    workspace: Path,
    threshold_tokens: int = LARGE_FILE_TOKEN_THRESHOLD,
    block_min_tokens: int = _BLOCK_MIN_TOKENS,
) -> HandlerResult:
    """
    Process a message, extracting large code blocks or whole-message pastes.

    Args:
        content: Raw message content.
        session_id: Used to namespace the extracted file directory.
        workspace: Nanobot workspace root (files go to workspace/files/).
        threshold_tokens: Minimum tokens for whole-message extraction.
        block_min_tokens: Minimum tokens for individual code block extraction.

    Returns:
        HandlerResult with processed_content and list of extracted files.
    """
    files_dir = workspace / "files"
    total_tokens = _estimate_tokens(content)
    extracted: list[ExtractedFile] = []

    # ── Strategy 1: Extract individual large code blocks ──────────────────────
    if total_tokens >= block_min_tokens:
        def _replace_block(m: re.Match) -> str:
            lang = m.group("lang").lower() if m.group("lang") else None
            code = m.group("code")
            block_tokens = _estimate_tokens(code)
            if block_tokens < block_min_tokens:
                return m.group(0)  # keep as-is
            ef = _extract_block(code, lang, session_id, files_dir)
            extracted.append(ef)
            return _make_stub(ef)

        processed = _CODE_BLOCK_RE.sub(_replace_block, content)

        # If we extracted something, return early
        if extracted:
            return HandlerResult(processed_content=processed, extracted=extracted)

    # ── Strategy 2: Whole-message extraction if still over threshold ───────────
    if total_tokens >= threshold_tokens:
        ef = _extract_block(content, language=None, session_id=session_id, files_dir=files_dir)
        extracted.append(ef)
        stub = _make_stub(ef)
        return HandlerResult(processed_content=stub, extracted=extracted)

    # No extraction needed
    return HandlerResult(processed_content=content, extracted=[])


def load_file(file_id: str, workspace: Path) -> str | None:
    """
    Load an extracted file by its file_id.

    Searches workspace/files/**/{file_id}.* and returns content, or None if not found.
    """
    files_dir = workspace / "files"
    if not files_dir.exists():
        return None

    # Search for matching file (exclude .summary.txt files)
    for candidate in files_dir.rglob(f"{file_id}.*"):
        if candidate.suffix != ".txt" or not candidate.name.endswith(".summary.txt"):
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                pass
        elif candidate.name == f"{file_id}.txt":
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                pass

    return None


def describe_file(file_id: str, workspace: Path) -> str | None:
    """
    Return a description of an extracted file (summary + location).

    Returns None if the file is not found.
    """
    files_dir = workspace / "files"
    if not files_dir.exists():
        return None

    for candidate in files_dir.rglob(f"{file_id}.*"):
        if candidate.name.endswith(".summary.txt"):
            continue
        summary_path = candidate.parent / f"{file_id}.summary.txt"
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else "(no summary)"
        size_kb = candidate.stat().st_size / 1024
        return (
            f"[file:{file_id}]\n"
            f"Path: {candidate}\n"
            f"Size: {size_kb:.1f} KB\n"
            f"Summary:\n{textwrap.indent(summary, '  ')}\n\n"
            f"Use load_file(\"{file_id}\", workspace) to retrieve full content."
        )

    return None
