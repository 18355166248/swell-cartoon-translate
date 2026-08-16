import pytest

from ctt.config import Config, _assign, find_config, load


def write(tmp_path, text):
    path = tmp_path / "ctt.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestDefaults:
    def test_no_file_yields_built_in_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config, warnings = load(use_env=False)
        assert config.source_path is None
        assert warnings == []
        assert config.target_lang == "zh-Hans"
        assert config.translate.backends == ["llamacpp"]

    def test_missing_explicit_path_is_an_error(self, tmp_path):
        # An unfindable default is fine; a path the user *named* is not.
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "nope.toml")


class TestLoading:
    def test_scalar_and_nested_values(self, tmp_path):
        path = write(tmp_path, """
target_lang = "zh-Hant"
[detect]
threshold = 0.5
[translate.llamacpp]
n_threads = 12
""")
        config, warnings = load(path, use_env=False)
        assert warnings == []
        assert config.target_lang == "zh-Hant"
        assert config.detect.threshold == 0.5
        assert config.translate.llamacpp.n_threads == 12
        # Untouched values keep their defaults.
        assert config.detect.model == "int8"

    def test_lists_and_tables(self, tmp_path):
        path = write(tmp_path, """
[ocr]
languages = ["latin", "korean"]
[glossary]
LIAM = "利亚姆"
""")
        config, _ = load(path, use_env=False)
        assert config.ocr.languages == ["latin", "korean"]
        assert config.glossary == {"LIAM": "利亚姆"}

    def test_unknown_keys_are_reported_not_ignored(self, tmp_path):
        # A typo'd setting silently doing nothing is the worst outcome.
        path = write(tmp_path, """
target_lang = "zh-Hans"
tarrget_lang = "oops"
[detect]
thresold = 0.9
""")
        config, warnings = load(path, use_env=False)
        assert len(warnings) == 2
        assert any("tarrget_lang" in w for w in warnings)
        assert any("detect.thresold" in w for w in warnings)

    def test_malformed_toml_names_the_file(self, tmp_path):
        path = write(tmp_path, "target_lang = [unclosed")
        with pytest.raises(ValueError, match="ctt.toml"):
            load(path)

    def test_found_by_searching_upward(self, tmp_path, monkeypatch):
        write(tmp_path, 'target_lang = "zh-Hant"')
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert find_config() == tmp_path / "ctt.toml"
        assert load(use_env=False)[0].target_lang == "zh-Hant"


class TestEnvironment:
    def test_env_overrides_file(self, tmp_path, monkeypatch):
        path = write(tmp_path, '[translate.llamacpp]\nmodel_path = "from-file.gguf"')
        monkeypatch.setenv("CTT_GGUF", "from-env.gguf")
        config, _ = load(path)
        assert config.translate.llamacpp.model_path == "from-env.gguf"

    def test_env_ignored_when_disabled(self, tmp_path, monkeypatch):
        path = write(tmp_path, '[translate.llamacpp]\nmodel_path = "from-file.gguf"')
        monkeypatch.setenv("CTT_GGUF", "from-env.gguf")
        config, _ = load(path, use_env=False)
        assert config.translate.llamacpp.model_path == "from-file.gguf"

    def test_empty_env_var_does_not_override(self, tmp_path, monkeypatch):
        path = write(tmp_path, 'target_lang = "zh-Hant"')
        monkeypatch.setenv("CTT_TARGET_LANG", "")
        assert load(path)[0].target_lang == "zh-Hant"


class TestAssign:
    def test_coerces_to_the_declared_type(self):
        # TOML gives real types but env vars are always strings.
        config = Config()
        _assign(config, "detect.threshold", "0.6")
        _assign(config, "translate.llamacpp.n_threads", "8")
        assert config.detect.threshold == 0.6
        assert isinstance(config.detect.threshold, float)
        assert config.translate.llamacpp.n_threads == 8
        assert isinstance(config.translate.llamacpp.n_threads, int)

    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("0", False), ("no", False), ("", False),
    ])
    def test_bool_strings(self, raw, expected):
        config = Config()
        _assign(config, "skip_thumbnails", raw)
        assert config.skip_thumbnails is expected

    def test_bool_is_not_coerced_to_int(self):
        # bool is a subclass of int; the int branch must not claim it.
        config = Config()
        _assign(config, "skip_thumbnails", False)
        assert config.skip_thumbnails is False
