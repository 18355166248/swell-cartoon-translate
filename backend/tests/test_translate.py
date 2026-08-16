import pytest

from ctt.translate.base import TranslationError, TranslatorChain
from ctt.translate.glossary import Glossary
from ctt.translate.llm import OpenAICompatTranslator


class Fake:
    """Backend that returns a canned answer per input, '' meaning 'refused'."""

    def __init__(self, name, mapping=None, raises=None, returns=None):
        self.name = name
        self.mapping = mapping or {}
        self.raises = raises
        self.returns = returns
        self.calls = []

    def translate(self, texts, source, target):
        self.calls.append(list(texts))
        if self.raises:
            raise self.raises
        if self.returns is not None:
            return self.returns
        return [self.mapping.get(t, "") for t in texts]


class TestTranslatorChain:
    def test_single_backend_passes_through(self):
        chain = TranslatorChain([Fake("a", {"hi": "你好"})])
        assert chain.translate(["hi"], "en", "zh-Hans") == ["你好"]

    def test_empty_backend_list_is_rejected(self):
        with pytest.raises(ValueError):
            TranslatorChain([])

    def test_refused_lines_fall_through_to_the_next_backend(self):
        """The behaviour the chain exists for.

        LLM services decline a share of adult dialogue and return empty
        strings rather than raising, so a refusal is invisible unless the
        chain treats an empty result as a gap to refill.
        """
        first = Fake("llm", {"ok": "好"})           # refuses "spicy"
        second = Fake("nllb", {"spicy": "辣", "ok": "行"})
        chain = TranslatorChain([first, second])

        assert chain.translate(["ok", "spicy"], "en", "zh-Hans") == ["好", "辣"]

    def test_only_the_gaps_are_retried(self):
        first = Fake("a", {"one": "一"})
        second = Fake("b", {"two": "二"})
        TranslatorChain([first, second]).translate(["one", "two"], "en", "zh-Hans")

        assert first.calls == [["one", "two"]]
        assert second.calls == [["two"]], "already-translated lines must not be re-sent"

    def test_partial_results_survive_a_later_backend_failing(self):
        first = Fake("a", {"one": "一"})
        second = Fake("b", raises=TranslationError("down"))
        result = TranslatorChain([first, second]).translate(["one", "two"], "en", "zh-Hans")
        assert result == ["一", ""]

    def test_a_raising_backend_is_skipped(self):
        chain = TranslatorChain([
            Fake("a", raises=TranslationError("no key")),
            Fake("b", {"hi": "你好"}),
        ])
        assert chain.translate(["hi"], "en", "zh-Hans") == ["你好"]

    def test_length_mismatch_is_discarded_rather_than_misaligned(self):
        """A short batch would otherwise shift every later line by one."""
        chain = TranslatorChain([
            Fake("bad", returns=["只有一个"]),
            Fake("good", {"a": "甲", "b": "乙"}),
        ])
        assert chain.translate(["a", "b"], "en", "zh-Hans") == ["甲", "乙"]

    def test_blank_inputs_are_never_sent(self):
        backend = Fake("a", {"hi": "你好"})
        result = TranslatorChain([backend]).translate(["hi", "", "   "], "en", "zh-Hans")
        assert result == ["你好", "", ""]
        assert backend.calls == [["hi"]]

    def test_untranslatable_lines_come_back_empty_not_missing(self):
        chain = TranslatorChain([Fake("a", {})])
        assert chain.translate(["x", "y"], "en", "zh-Hans") == ["", ""]


class TestGlossary:
    def test_relevant_entries_only(self):
        g = Glossary({"Yeong-sik": "永植", "Hyuna": "泫雅", "Absent": "缺席"})
        assert g.relevant_to(["Hello Hyuna!"]) == {"Hyuna": "泫雅"}

    def test_longest_key_wins(self):
        g = Glossary({"Yeong": "永", "Yeong-sik": "永植"})
        assert "Yeong-sik" in g.relevant_to(["Yeong-sik is here"])

    def test_prompt_hint_is_empty_when_nothing_matches(self):
        assert Glossary({"A": "甲"}).as_prompt_hint(["nothing here"]) == ""

    def test_violations_report_missing_agreed_terms(self):
        g = Glossary({"Hyuna": "泫雅"})
        assert g.violations("Hyuna smiled", "小雅笑了") == [("Hyuna", "泫雅")]
        assert g.violations("Hyuna smiled", "泫雅笑了") == []

    def test_violations_ignore_terms_absent_from_the_source(self):
        g = Glossary({"Hyuna": "泫雅"})
        assert g.violations("someone smiled", "有人笑了") == []


class TestNumberedOutputParsing:
    parse = staticmethod(OpenAICompatTranslator._parse)

    def test_json_object(self):
        assert self.parse('{"1": "一", "2": "二"}', 2) == ["一", "二"]

    def test_json_with_prose_keys(self):
        assert self.parse('{"line 1": "一", "line 2": "二"}', 2) == ["一", "二"]

    def test_falls_back_to_numbered_lines(self):
        # response_format is advisory on local servers; plain text is common.
        assert self.parse("1. 一\n2. 二", 2) == ["一", "二"]

    def test_partial_output_leaves_gaps_for_the_chain(self):
        assert self.parse('{"1": "一"}', 3) == ["一", "", ""]

    def test_out_of_range_indices_are_ignored(self):
        assert self.parse('{"1": "一", "9": "九"}', 2) == ["一", ""]

    def test_unparseable_output_raises(self):
        with pytest.raises(TranslationError):
            self.parse("I cannot help with that.", 2)
