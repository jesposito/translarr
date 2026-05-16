"""Shared LLM prompt-assembly + line-count reconciliation helpers.

These helpers used to be duplicated inline in every provider. The tests
here pin down the contract so all providers behave consistently.
"""

from server.llm.base import build_user_message, reconcile_lines


class TestBuildUserMessage:
    def test_plain_lines_only(self) -> None:
        msg = build_user_message(["hello", "world"])
        assert "Translate the following 2 lines:" in msg
        assert "hello" in msg
        assert "world" in msg
        assert "Previously translated context" not in msg
        assert "Glossary" not in msg

    def test_includes_prior_context_block(self) -> None:
        msg = build_user_message(
            ["new line"],
            prior_context=["already translated 1", "already translated 2"],
        )
        assert "Previously translated context:" in msg
        assert "already translated 1" in msg
        assert "already translated 2" in msg
        assert msg.index("Previously translated context:") < msg.index("Translate the following")

    def test_prior_context_caps_at_ten_lines(self) -> None:
        context = [f"line {i}" for i in range(20)]
        msg = build_user_message(["x"], prior_context=context)
        # Only the last 10 should be included.
        assert "line 19" in msg
        assert "line 10" in msg
        assert "line 9" not in msg
        assert "line 0" not in msg

    def test_glossary_block_renders_each_pair(self) -> None:
        glossary = {"Nezuko": "Nezuko", "Hashira": "Pillar"}
        msg = build_user_message(["x"], glossary=glossary)
        assert "Glossary (preserve these exactly):" in msg
        assert "  Nezuko -> Nezuko" in msg
        assert "  Hashira -> Pillar" in msg

    def test_empty_glossary_omits_block(self) -> None:
        msg = build_user_message(["x"], glossary={})
        assert "Glossary" not in msg

    def test_empty_prior_context_omits_block(self) -> None:
        msg = build_user_message(["x"], prior_context=[])
        assert "Previously translated" not in msg

    def test_context_then_glossary_then_lines(self) -> None:
        msg = build_user_message(
            ["new"],
            prior_context=["ctx"],
            glossary={"a": "b"},
        )
        ctx_idx = msg.index("Previously translated")
        gloss_idx = msg.index("Glossary")
        trans_idx = msg.index("Translate the following")
        assert ctx_idx < gloss_idx < trans_idx


class TestReconcileLines:
    def test_matching_length_passes_through(self) -> None:
        assert reconcile_lines(["a", "b", "c"], 3) == ["a", "b", "c"]

    def test_pads_when_short(self) -> None:
        assert reconcile_lines(["a"], 3) == ["a", "", ""]

    def test_truncates_when_long(self) -> None:
        assert reconcile_lines(["a", "b", "c", "d", "e"], 3) == ["a", "b", "c"]

    def test_strips_blank_lines_when_expected_multi(self) -> None:
        # Models often emit trailing blank lines or paragraph spacers.
        assert reconcile_lines(["a", "", "b", "", "c"], 3) == ["a", "b", "c"]

    def test_preserves_single_blank_when_expected_one(self) -> None:
        # A single empty line is meaningful when only one is expected.
        assert reconcile_lines([""], 1) == [""]

    def test_pads_then_truncates_handles_only_blanks(self) -> None:
        # All blanks get stripped, then padded to expected length.
        assert reconcile_lines(["", "", ""], 2) == ["", ""]
