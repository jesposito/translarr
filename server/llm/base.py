import re
from typing import Protocol


class LLMProvider(Protocol):
    """Pluggable LLM backend. Implementations live in sibling modules."""

    name: str
    model: str

    async def translate_batch(
        self,
        lines: list[str],
        source_lang: str,
        target_lang: str,
        prior_context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        """Translate a batch of subtitle lines in order, returning same-length list."""
        ...


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


TRANSLATE_SYSTEM_PROMPT = (
    "You are a subtitle translator. You translate subtitle lines from "
    "{source_lang} to {target_lang} for video playback.\n\n"
    "Rules:\n"
    "1. Output the SAME NUMBER of lines as the input, in the SAME ORDER. "
    "One translated line per input line.\n"
    "2. Preserve speaker tags, sound effects in brackets ([explosion], [sigh]), "
    "and onomatopoeia.\n"
    "3. Preserve ASS/SSA style tags like {{\\an8}}, {{\\fad(...)}}, "
    "{{\\c&H...&}}. Do not translate them.\n"
    "4. Keep subtitle lines short and readable. If a translation is too long "
    "for its display time, prefer shorter wording over verbose accuracy.\n"
    "5. Use the prior context (already-translated previous lines) to resolve "
    "pronouns and ambiguity.\n"
    "6. Keep names, attack names, and world-specific terms consistent with "
    "the glossary if one is provided.\n"
    "7. Match the register and tone of the original. Formal stays formal, "
    "casual stays casual.\n\n"
    "Output ONLY the translated lines, one per line, in order. No numbering, "
    "no commentary, no markdown."
)


def build_user_message(
    lines: list[str],
    prior_context: list[str] | None = None,
    glossary: dict[str, str] | None = None,
) -> str:
    """Build the user prompt from lines, optional context, and glossary.

    Extracted so all providers share the same prompt assembly logic
    instead of duplicating it across five files.
    """
    parts: list[str] = []
    if prior_context:
        parts.append("Previously translated context:")
        parts.extend(prior_context[-10:])
        parts.append("---")
    if glossary:
        parts.append("Glossary (preserve these exactly):")
        for src, dst in glossary.items():
            parts.append(f"  {src} -> {dst}")
        parts.append("---")
    parts.append(f"Translate the following {len(lines)} lines:")
    parts.extend(lines)
    return "\n".join(parts)


def reconcile_lines(translated: list[str], expected: int) -> list[str]:
    """Force output line count to match expected input count.

    Strips wrapper noise first (<think> blocks from reasoning models, and
    markdown code fences), since each stray line would shift every later
    translation onto the wrong cue. Blank lines and a leading preamble
    ("Here are the translations:") are dropped only when the model
    returned too many lines, so a genuinely blank cue keeps its slot.
    Short output is padded with empty strings, which the pipeline replaces
    with the original text.
    """
    text = _THINK_RE.sub("", "\n".join(translated)).strip("\n")
    out = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
    if len(out) > expected:
        out = [ln for ln in out if ln.strip() != ""] or [""]
    if len(out) > expected and out[0].rstrip().endswith(":"):
        out = out[1:]
    if len(out) < expected:
        out += [""] * (expected - len(out))
    elif len(out) > expected:
        out = out[:expected]
    return out
