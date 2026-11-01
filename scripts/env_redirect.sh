# Source this file to use portable caches; existing environment overrides win.
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/pip}"
export HF_HOME="${HF_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
