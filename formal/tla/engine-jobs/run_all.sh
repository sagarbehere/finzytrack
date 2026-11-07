#!/bin/sh
# Run the engine-jobs TLC configurations listed in configs.txt; see HOW_TO_RUN.md (options: ../run_model.sh).
HERE=$(cd "$(dirname "$0")" && pwd)
exec sh "$HERE/../run_model.sh" "$HERE" EngineJobs.tla "Engine-jobs model" "$@"
