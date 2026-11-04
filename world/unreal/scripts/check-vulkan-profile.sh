#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
engine="${UE_ROOT:?}/Engine"
output="$PWD/.local/toolchains/vulkan-profile-check"
c++ --version
c++ -std=c++17 -O2 -I"$engine/Source/ThirdParty/Vulkan/Include" \
  -I"$engine/Source/ThirdParty/Vulkan/profiles/include" \
  world/unreal/scripts/check-vulkan-profile.cpp -l:libvulkan.so.1 -o "$output"
VK_DRIVER_FILES=/etc/vulkan/icd.d/nvidia_icd.json "$output"
