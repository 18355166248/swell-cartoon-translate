import pytest

from ctt import runtime


class TestProfiles:
    @pytest.mark.parametrize("name", ["performance", "balanced", "background"])
    def test_every_profile_resolves(self, name):
        assert runtime.get_profile(name).name == name

    def test_unknown_profile_falls_back_to_balanced(self):
        # A typo in ctt.toml must not silently mean "full speed".
        assert runtime.get_profile("turbo").name == "balanced"

    def test_only_performance_may_use_the_gpu(self):
        # This is the switch: anything else hands the card back to a game.
        assert runtime.get_profile("performance").gpu is True
        assert runtime.get_profile("balanced").gpu is False
        assert runtime.get_profile("background").gpu is False

    def test_background_uses_fewer_threads(self):
        assert runtime.threads_for("background") < runtime.threads_for("performance")

    def test_threads_are_based_on_physical_cores(self):
        # llama.cpp gains nothing from hyper-threads and loses to the
        # contention, so the full-speed profile must not exceed physical cores.
        assert runtime.threads_for("performance") == runtime.physical_cores()

    def test_thread_count_is_never_zero(self):
        for name in ("performance", "balanced", "background"):
            assert runtime.threads_for(name) >= 1


class TestGpuLayers:
    def test_non_gpu_profiles_get_zero(self):
        assert runtime.gpu_layers_for("balanced") == 0
        assert runtime.gpu_layers_for("background") == 0

    def test_non_gpu_profiles_ignore_an_explicit_request(self):
        # Otherwise a stale ctt.toml value would keep the card busy after the
        # user switched profile to play something.
        assert runtime.gpu_layers_for("background", 24) == 0

    def test_explicit_request_is_honoured_on_a_gpu_profile(self, monkeypatch):
        monkeypatch.setattr(runtime, "get_profile",
                            lambda n: runtime._PROFILES["performance"])
        monkeypatch.setattr("ctt.cuda.available", lambda: True)
        assert runtime.gpu_layers_for("performance", 6) == 6

    def test_zero_is_a_valid_explicit_request(self, monkeypatch):
        monkeypatch.setattr("ctt.cuda.available", lambda: True)
        assert runtime.gpu_layers_for("performance", 0) == 0

    def test_auto_is_capped_at_the_measured_best(self, monkeypatch):
        # Past 12 layers it got slower, not faster -- the card runs out of
        # room and the spilling costs more than the offload saves.
        monkeypatch.setattr("ctt.cuda.available", lambda: True)
        monkeypatch.setattr("ctt.cuda.free_vram_mb", lambda: 99_999)
        assert runtime.gpu_layers_for("performance") == runtime.MAX_GPU_LAYERS

    def test_auto_shrinks_when_vram_is_tight(self, monkeypatch):
        monkeypatch.setattr("ctt.cuda.available", lambda: True)
        monkeypatch.setattr("ctt.cuda.free_vram_mb", lambda: 1200)
        layers = runtime.gpu_layers_for("performance")
        assert 0 < layers < runtime.MAX_GPU_LAYERS

    def test_auto_yields_nothing_when_the_card_is_full(self, monkeypatch):
        # A game already has it; asking for layers would fail the load.
        monkeypatch.setattr("ctt.cuda.available", lambda: True)
        monkeypatch.setattr("ctt.cuda.free_vram_mb", lambda: 200)
        assert runtime.gpu_layers_for("performance") == 0

    def test_no_cuda_build_means_no_layers(self, monkeypatch):
        monkeypatch.setattr("ctt.cuda.available", lambda: False)
        assert runtime.gpu_layers_for("performance") == 0

    def test_unmeasurable_vram_uses_the_measured_default(self, monkeypatch):
        monkeypatch.setattr("ctt.cuda.available", lambda: True)
        monkeypatch.setattr("ctt.cuda.free_vram_mb", lambda: -1)
        assert runtime.gpu_layers_for("performance") == runtime.MAX_GPU_LAYERS


class TestDescribe:
    def test_reports_what_the_ui_needs(self):
        for entry in runtime.describe():
            assert {"name", "threads", "gpu", "gpu_layers", "description"} <= entry.keys()
            assert entry["description"]

    def test_non_gpu_profiles_report_zero_layers(self):
        by_name = {e["name"]: e for e in runtime.describe()}
        assert by_name["balanced"]["gpu_layers"] == 0
        assert by_name["background"]["gpu_layers"] == 0


class TestApplyPriority:
    def test_returns_what_it_did(self):
        result = runtime.apply_priority("background")
        assert isinstance(result, str) and result
        runtime.apply_priority("performance")

    def test_never_raises_on_an_unknown_profile(self):
        runtime.apply_priority("nonsense")
        runtime.apply_priority("performance")
