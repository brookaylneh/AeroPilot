#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
test "$#" = 1
sam_image=$1
tar -cf - \
  mission_planner/__init__.py \
  mission_planner/environment.py \
  mission_planner/agents/__init__.py \
  mission_planner/agents/services/__init__.py \
  mission_planner/agents/services/sam.Dockerfile \
  mission_planner/agents/services/vision-requirements.txt \
  mission_planner/agents/services/assemble_sam.py \
  mission_planner/agents/services/sam_video.py \
  mission_planner/agents/services/vision.py \
  | docker build --file mission_planner/agents/services/sam.Dockerfile --tag "$sam_image" -
