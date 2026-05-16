"""Shared webhook helpers — currently the tag-extraction logic that
Sonarr and Radarr handlers both delegate to."""

from server.webhooks.common import has_translate_tag


class TestHasTranslateTag:
    def test_string_tag_matches(self) -> None:
        payload = {"movie": {"tags": ["translate", "4k"]}}
        assert has_translate_tag(payload, "movie", "translate") is True

    def test_object_tag_matches_on_label(self) -> None:
        payload = {"series": {"tags": [{"id": 1, "label": "translate"}]}}
        assert has_translate_tag(payload, "series", "translate") is True

    def test_mixed_tag_array_supported(self) -> None:
        payload = {"movie": {"tags": ["4k", {"id": 2, "label": "translate"}]}}
        assert has_translate_tag(payload, "movie", "translate") is True

    def test_no_match_returns_false(self) -> None:
        payload = {"series": {"tags": ["uhd", "4k"]}}
        assert has_translate_tag(payload, "series", "translate") is False

    def test_missing_entity_returns_false(self) -> None:
        # Different webhook arrived with no expected top-level key.
        assert has_translate_tag({}, "movie", "translate") is False

    def test_null_entity_returns_false(self) -> None:
        payload = {"movie": None}
        assert has_translate_tag(payload, "movie", "translate") is False

    def test_missing_tags_array_returns_false(self) -> None:
        payload = {"movie": {"title": "Foo"}}
        assert has_translate_tag(payload, "movie", "translate") is False

    def test_null_tags_array_returns_false(self) -> None:
        payload = {"movie": {"tags": None}}
        assert has_translate_tag(payload, "movie", "translate") is False

    def test_unknown_tag_shape_is_ignored(self) -> None:
        # Future arr versions could ship ints — we shouldn't crash.
        payload = {"movie": {"tags": [42, None, ["nested"]]}}
        assert has_translate_tag(payload, "movie", "translate") is False

    def test_case_sensitive_match(self) -> None:
        payload = {"movie": {"tags": ["Translate"]}}
        assert has_translate_tag(payload, "movie", "translate") is False
