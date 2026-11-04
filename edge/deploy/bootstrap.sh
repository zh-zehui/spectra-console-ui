#!/usr/bin/env bash
# Prepare the runtime around an existing GNU Radio/UHD environment. No RF task.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
if [ "$project_root" != /home/sribd/spectrum_agent ]; then
  echo 'This snapshot uses /home/sribd/spectrum_agent in its deployed scripts and config.' >&2
  echo 'Use that deployment layout, or adapt those paths before installation.' >&2
  exit 1
fi
radio_prefix="${SPECTRA_RADIO_PREFIX:-/home/sribd/miniforge3/envs/usrp_dev}"
test -x "$radio_prefix/bin/python"
"$radio_prefix/bin/python" -c 'import numpy, scipy, uhd; from gnuradio import gr; print("Radio environment:", gr.version())'
if [ ! -e .venv ]; then
  "$radio_prefix/bin/python" -m venv --system-site-packages .venv
fi
.venv/bin/python -m pip install -r requirements.txt
mkdir -p runs artifacts runtime ui/dist
if [ ! -x runtime/node/bin/node ]; then
  if [ -n "${SPECTRA_NODE_PREFIX:-}" ]; then
    node_prefix="$SPECTRA_NODE_PREFIX"
  else
    node_binary="$(readlink -f "$(command -v node)")"
    node_prefix="$(dirname -- "$(dirname -- "$node_binary")")"
  fi
  test -x "$node_prefix/bin/node"
  test ! -e runtime/node && test ! -L runtime/node
  ln -s "$node_prefix" runtime/node
fi
export PATH="$project_root/runtime/node/bin:$PATH"
node -e 'if(Number(process.versions.node.split(".")[0])<24)throw Error("Use Node.js 24+; deployed baseline is 24.22.1");console.log("Node:",process.version)'
(cd runtime/dsh && npm ci)
if [ ! -e node_modules ] && [ ! -L node_modules ]; then
  ln -s runtime/dsh/node_modules node_modules
elif [ "$(readlink -f node_modules)" != "$(readlink -f runtime/dsh/node_modules)" ]; then
  echo 'Existing node_modules is not the project DSH dependency tree; left unchanged.' >&2
  exit 1
fi
if [ -n "${SPECTRA_FRONTEND_DIST:-}" ]; then
  test -f "$SPECTRA_FRONTEND_DIST/index.html"
  cp -a "$SPECTRA_FRONTEND_DIST/." ui/dist/
fi
test -f ui/dist/index.html
echo 'Python/DSH/frontend prepared. Next: restore vendor sources and build the selected receivers.'
