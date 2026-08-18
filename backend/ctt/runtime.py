"""CPU scheduling for the translation job.

The pipeline is CPU-bound and long-running: a chapter occupies every core for
hours. That is fine on an idle machine and miserable if you want to use it for
anything else meanwhile.

Priority, not thread count, is the lever that matters. On this 6-core i5 the
translator already runs `n_threads=6` (llama.cpp is fastest at one thread per
*physical* core), so cutting threads costs throughput immediately. Lowering
priority costs almost nothing while the machine is idle -- the scheduler only
takes cycles away when something else actually wants them.

Memory is not adjustable in any useful way: measured across n_ctx 512..4096 the
resident set moved 6383MB -> 6594MB, so essentially all of it is the model
weights. Wanting a smaller footprint means wanting a smaller model.
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass

log = logging.getLogger(__name__)

PROFILES = ("performance", "balanced", "background")


@dataclass(frozen=True)
class Profile:
    """How much of the machine a run may take."""

    name: str
    nice: int
    """Higher is politer. Mapped to Windows priority classes below."""
    thread_fraction: float
    """Share of physical cores to use, when the caller asks for a thread count."""
    gpu: bool
    """Whether this profile may offload layers to the GPU.

    Off for anything but `performance`: the card is what a game needs, and
    offloading takes ~2GB of a 4GB board. Switching profile is how you hand
    it back.
    """
    description: str


# Wall time and CPU below are measured, not estimated: one llama.cpp
# translation of three lines on a 6-core i5-11600KF, percentages against the
# 12 logical cores psutil reports.
#
#   performance  6 threads  19.9s  795% avg
#   balanced     6 threads  19.8s  795% avg
#   background   3 threads  20.4s  605% avg
#
# The surprise is `background`: halving the threads costs 2.5% of the wall
# time while giving back a quarter of the CPU. This workload is bound by
# memory bandwidth rather than arithmetic, so the extra cores were mostly
# waiting on RAM anyway.
_PROFILES: dict[str, Profile] = {
    "performance": Profile(
        "performance", nice=0, thread_fraction=1.0, gpu=True,
        description="全速 + GPU 卸载。实测 1.54x（36.9s → 23.9s），占约 2GB 显存。"
                    "挂机跑用这个，玩游戏前切走",
    ),
    "balanced": Profile(
        "balanced", nice=10, thread_fraction=1.0, gpu=False,
        description="纯 CPU，线程数不变，只降优先级。空闲时和全速一样快"
                    "（实测 19.8s vs 19.9s），你要用机器时系统会先让给你。显存完全不占",
    ),
    "background": Profile(
        "background", nice=19, thread_fraction=0.5, gpu=False,
        description="纯 CPU，只用一半核心 + 最低优先级。实测只慢 2.5%（20.4s）"
                    "却少占 24% CPU——这个负载卡在内存带宽上，多给核心也是在等内存。"
                    "打游戏选这个",
    ),
}

MAX_GPU_LAYERS = 12
"""Layers to offload when a profile allows the GPU.

Measured on a 4GB GTX 1650 SUPER with ~2.2GB free: 8 layers 1.27x,
**12 layers 1.54x**, 16 layers 1.37x, 20+ slower than CPU. Past the sweet spot
the card runs out of room and the spilling costs more than the offload saves.

12 is also where the output stopped matching the CPU run. CUDA and CPU kernels
round differently, and with enough layers on the GPU that flips a token now and
then -- not worse, but not identical either, so the default stays at the last
setting measured to agree.
"""

VRAM_HEADROOM_MB = 400
"""Left free for whatever else is on screen. The desktop alone holds several
hundred megabytes, and taking the last of it stalls the display."""

LAYER_COST_MB = 165
"""Rough VRAM per offloaded layer for a 7B Q4_K_M, from the measurements
above (12 layers ≈ 2.0GB)."""


def get_profile(name: str) -> Profile:
    return _PROFILES.get(name, _PROFILES["balanced"])


def physical_cores() -> int:
    try:
        import psutil

        return psutil.cpu_count(logical=False) or os.cpu_count() or 4
    except ImportError:
        # Logical count halved is the right guess on any hyper-threaded chip,
        # and harmless on one without.
        return max(1, (os.cpu_count() or 4) // 2)


def threads_for(profile_name: str) -> int:
    """Thread count for llama.cpp under a profile.

    Based on *physical* cores. llama.cpp gains nothing from hyper-threads and
    often loses to the extra contention.
    """
    profile = get_profile(profile_name)
    return max(1, round(physical_cores() * profile.thread_fraction))


def gpu_layers_for(profile_name: str, requested: int = -1) -> int:
    """How many layers to offload under a profile.

    `requested` of -1 means decide from free VRAM; a non-negative value is an
    explicit override. Either way a profile with `gpu=False` gets 0 -- that is
    the switch that hands the card back to a game.

    Sized against *free* VRAM rather than total, because the card is shared
    with whatever is already on screen and that changes minute to minute.
    """
    profile = get_profile(profile_name)
    if not profile.gpu:
        return 0

    from .cuda import available, free_vram_mb

    if not available():
        # CPU-only build, or no usable driver. Not an error -- just no GPU.
        return 0

    if requested >= 0:
        return requested

    free = free_vram_mb()
    if free < 0:
        # Cannot measure; the measured-good default is safer than guessing high.
        return MAX_GPU_LAYERS

    affordable = int((free - VRAM_HEADROOM_MB) / LAYER_COST_MB)
    return max(0, min(MAX_GPU_LAYERS, affordable))


def apply_priority(profile_name: str) -> str:
    """Lower this process's scheduling priority. Returns what was applied.

    Applies to the whole process, not one thread: llama.cpp and ONNX Runtime
    both spawn their own worker pools, and those workers are what actually
    consume the cores. A thread-level change would miss all of them.
    """
    profile = get_profile(profile_name)

    try:
        import psutil

        process = psutil.Process()
        if platform.system() == "Windows":
            classes = {
                0: psutil.NORMAL_PRIORITY_CLASS,
                10: psutil.BELOW_NORMAL_PRIORITY_CLASS,
                19: psutil.IDLE_PRIORITY_CLASS,
            }
            target = classes.get(profile.nice, psutil.BELOW_NORMAL_PRIORITY_CLASS)
            process.nice(target)
            # Deprioritise disk I/O too. Page images are several MB each, and
            # on a busy machine that is enough to make a game stutter.
            try:
                process.ionice(psutil.IOPRIO_LOW if profile.nice else psutil.IOPRIO_NORMAL)
            except (AttributeError, OSError):
                pass
            return f"{profile.name} (priority class {target})"

        process.nice(profile.nice)
        return f"{profile.name} (nice {profile.nice})"

    except ImportError:
        if hasattr(os, "nice") and profile.nice:
            os.nice(profile.nice)
            return f"{profile.name} (os.nice {profile.nice})"
        log.info("psutil not installed; leaving priority alone")
        return "unchanged"
    except Exception as exc:  # noqa: BLE001 - never fail a run over priority
        log.warning("could not set priority: %s", exc)
        return "unchanged"


def describe() -> list[dict]:
    """Profile list for the settings UI."""
    return [
        {
            "name": p.name,
            "threads": threads_for(p.name),
            "gpu": p.gpu,
            "gpu_layers": gpu_layers_for(p.name),
            "description": p.description,
        }
        for p in _PROFILES.values()
    ]
