#!/usr/bin/env bash
# 一键初始化（Linux / macOS）。Windows 用 scripts/setup.ps1。
#
# 幂等：重复跑是安全的，已装好的会跳过。中途失败直接重跑，不用先清理。
#
#   ./scripts/setup.sh              完整初始化
#   ./scripts/setup.sh --skip-models  只装依赖，不下 4.7GB 模型

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_MODELS=0
FAILED=()

for arg in "$@"; do
  case "$arg" in
    --skip-models) SKIP_MODELS=1 ;;
    *) echo "未知参数: $arg"; exit 1 ;;
  esac
done

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  [OK]   %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; FAILED+=("$1"); }

has_module() { python3 -c "import $1" 2>/dev/null; }

# ------------------------------------------------------------------ Python ---
step "检查 Python"
if ! command -v python3 >/dev/null 2>&1; then
  fail "找不到 python3，请先安装 Python 3.11+"
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  fail "需要 Python 3.11+（tomllib 是 3.11 才进标准库的），当前 $PY_VER"
  exit 1
fi
ok "Python $PY_VER"

# -------------------------------------------------------------------- 依赖 ---
step "安装 Python 依赖"
install_group() {
  local name="$1" probe="$2"; shift 2
  if has_module "$probe"; then ok "$name 已安装，跳过"; return; fi
  echo "  安装 $name..."
  python3 -m pip install --quiet "$@" >/dev/null 2>&1
  if has_module "$probe"; then ok "$name"; else fail "$name 安装失败: pip install $*"; fi
}

install_group "核心" cv2          numpy opencv-python-headless pillow pydantic
install_group "检测" onnxruntime  onnxruntime huggingface-hub
install_group "OCR"  paddleocr    paddlepaddle paddleocr
install_group "翻译" llama_cpp    llama-cpp-python
install_group "服务" fastapi      fastapi "uvicorn[standard]" python-multipart
install_group "测试" pytest       pytest

# -------------------------------------------------------------------- 字体 ---
# 字体检查和模型下载都调 scripts/bootstrap.py，与 setup.ps1 共用同一份逻辑。
step "检查中文字体"
if python3 "$ROOT/scripts/bootstrap.py" --check-fonts; then
  ok "字体就绪"
else
  warn "没找到中文字体，排版会渲染成方块"
fi

# -------------------------------------------------------------------- 模型 ---
if [ "$SKIP_MODELS" -eq 1 ]; then
  step "模型下载"
  warn "已跳过（--skip-models）。首次翻译时会自动下载。"
else
  step "下载模型（约 4.7GB，只需一次）"
  if python3 "$ROOT/scripts/bootstrap.py" --models; then
    ok "模型就绪"
  else
    fail "模型下载失败，重跑本脚本即可续传"
  fi
fi

# -------------------------------------------------------------------- 自检 ---
step "自检"
if (cd "$ROOT/backend" && python3 -m pytest tests/ -q 2>&1 | tail -1); then
  ok "测试通过"
else
  fail "测试未通过"
fi
(cd "$ROOT/backend" && python3 -m ctt.cli config 2>&1 | head -2)

# -------------------------------------------------------------------- 收尾 ---
echo
if [ "${#FAILED[@]}" -eq 0 ]; then
  cat <<'EOF'
初始化完成。

下一步：
  1. 把漫画图片放进 assets/ （或任意目录）
  2. cd backend
  3. python3 -m ctt.cli translate ../assets -o ../out

配置改 ctt.toml，查看当前生效配置：python3 -m ctt.cli config
需要密钥的后端用环境变量：DEEPL_API_KEY / CTT_LLM_KEY
EOF
else
  printf '有 %d 项未完成：\n' "${#FAILED[@]}"
  printf '  - %s\n' "${FAILED[@]}"
  printf '\n修好后重跑本脚本，已完成的部分会自动跳过。\n'
  exit 1
fi
