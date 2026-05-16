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

    LLMs occasionally drop or merge adjacent lines. We pad with empty
    strings (so the original text passes through in the pipeline) or
    truncate as needed.
    """
    out = [ln for ln in translated if ln.strip() != "" or expected == 1]
    if len(out) < expected:
        out += [""] * (expected - len(out))
    elif len(out) > expected:
        out = out[:expected]
    return out
