#!/bin/sh
# Run the engine-jobs TLC configurations listed in configs.txt; see HOW_TO_RUN.md.
# Plain POSIX shell and Java: no AI, no network except the one-time jar download.
#
#   run_all.sh [--tier S,M,L] [--kind base,mutant] [--only NAME,NAME]
#              [--workers N] [--heap 8g] [--force] [--list]
#
# - One log per configuration: results/<name>.log. results/SUMMARY.md is rewritten after each one:
#   result, expected result, distinct states, depth and wall time per configuration.
# - A configuration whose log already holds a verdict for the same model, .cfg, jar and Java
#   version is skipped (so an interrupted run resumes); --force runs it again.
# - A configuration of mode "simulate" runs TLC's bounded random simulation (SIM_TRACES traces of at
#   most SIM_DEPTH steps), not an exhaustive check.
# - Ctrl-C stops the running TLC, deletes its state folder, writes the summary and exits.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
TLA_DIR=$(cd "$HERE/.." && pwd)
JAR="$TLA_DIR/tools/tla2tools.jar"
RES="$HERE/results"
MODEL="$HERE/EngineJobs.tla"
SIM_TRACES=${SIM_TRACES:-200000}; SIM_DEPTH=${SIM_DEPTH:-200}
LIST="$HERE/configs.txt"

TIERS="S,M,L"; KINDS="base,mutant"; ONLY=""; WORKERS=""; HEAP=""; FORCE=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIERS=$2; shift 2 ;;
    --kind) KINDS=$2; shift 2 ;;
    --only) ONLY=$2; shift 2 ;;
    --workers) WORKERS=$2; shift 2 ;;
    --heap) HEAP=$2; shift 2 ;;
    --force) FORCE=1; shift ;;
    --list) DRY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

inlist() { case ",$2," in *",$1,"*) return 0 ;; esac; return 1; }
sha256() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 | awk '{print $1}'
  else sha256sum | awk '{print $1}'; fi
}
cores() { sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 2; }

selected() {  # name expect tier kind
  if [ -n "$ONLY" ]; then inlist "$1" "$ONLY"; return; fi
  inlist "$3" "$TIERS" && inlist "$4" "$KINDS"
}

if [ "$DRY" = 1 ]; then
  grep -v '^#' "$LIST" | while read -r name expect tier kind mode; do
    [ -n "$name" ] || continue
    selected "$name" "$expect" "$tier" "$kind" && echo "$name  $tier  $kind  expect $expect"
  done
  exit 0
fi

command -v java >/dev/null 2>&1 || { echo "Java is not installed (Java 21 is needed)" >&2; exit 1; }
sh "$TLA_DIR/fetch_tools.sh" || exit 1
[ -n "$WORKERS" ] || WORKERS=$(cores)
if [ -n "$HEAP" ]; then MEM="-Xmx$HEAP"; else MEM="-XX:MaxRAMPercentage=50"; fi
mkdir -p "$RES"
# the resume key: model, .cfg, the jar's bytes and the Java version
TOOLKEY="$(sha256 < "$JAR") $(java -version 2>&1 | head -1)"

verdict() {  # log cfg mode -> PASS | VIOLATE:<property> | ERROR | INCOMPLETE
  if grep -q "No error has been found" "$1"; then echo PASS; return; fi
  # a simulation that ended without an error found none in the traces it walked
  if [ "${3:-check}" = simulate ] && ! grep -q "^Error:" "$1" && grep -q "^Finished in" "$1"; then
    echo PASS; return
  fi
  # a configuration that checks one property: any violation TLC reports is that property's
  # (TLC names the violated conjunct, e.g. TruthInv inside OutcomeTruthful)
  one=$(awk '$1 == "INVARIANT" || $1 == "PROPERTY" {print $2}' "$2")
  if [ "$(echo "$one" | wc -l | tr -d ' ')" = 1 ] && [ -n "$one" ] \
     && grep -q -E "^Error: (Invariant .* is violated|Action property .* is violated|Temporal properties were violated)" "$1"; then
    echo "VIOLATE:$one"; return
  fi
  inv=$(sed -n 's/^Error: Invariant \([A-Za-z0-9_]*\) is violated.*/\1/p' "$1" | head -1)
  if [ -n "$inv" ]; then echo "VIOLATE:$inv"; return; fi
  act=$(sed -n 's/^Error: Action property \([A-Za-z_][A-Za-z0-9_]*\) is violated.*/\1/p' "$1" | head -1)
  if [ -n "$act" ]; then echo "VIOLATE:$act"; return; fi
  if grep -q -E "^Error: (Action property|Temporal properties were violated|Invariant line)" "$1"; then
    props=$(awk '$1 == "PROPERTY" {print $2}' "$2")
    if [ "$(echo "$props" | wc -l | tr -d ' ')" = 1 ] && [ -n "$props" ]; then echo "VIOLATE:$props"
    else echo "VIOLATE:$(grep -m1 -E '^Error: (Action property|Temporal|Invariant line)' "$1" | cut -c8-60 | tr ' ' '_')"; fi
    return
  fi
  if grep -q "^Error:" "$1"; then echo ERROR; return; fi
  echo INCOMPLETE
}

summary() {
  out="$RES/SUMMARY.md"
  {
    echo "# Engine-jobs model: results"
    echo
    echo "Written by run_all.sh at $(date '+%Y-%m-%d %H:%M:%S'). Model sha256 $(sha256 < "$MODEL" | cut -c1-12)."
    echo
    echo "| Config | Tier | Kind | Expected | Result | As expected | Distinct states | Depth | Wall time (s) | Distinct states/s |"
    echo "|---|---|---|---|---|---|---|---|---|---|"
  } > "$out"
  grep -v '^#' "$LIST" | while read -r name expect tier kind mode; do
    [ -n "$name" ] || continue
    log="$RES/$name.log"
    [ -f "$log" ] || continue
    res=$(sed -n 's/^RUNNER-VERDICT //p' "$log" | tail -1)
    [ -n "$res" ] || res="running or stopped"
    secs=$(sed -n 's/^RUNNER-SECONDS //p' "$log" | tail -1)
    # the final count, or the last progress report of a run that was stopped
    ds=$(sed -n 's/.* states generated, \([0-9,]*\) distinct states found.*/\1/p' "$log" | tail -1)
    [ -n "$ds" ] || ds=$(sed -n 's/^Progress.*generated ([0-9,]* s\/min), \([0-9,]*\) distinct states found.*/\1/p' "$log" | tail -1)
    depth=$(sed -n 's/.*depth of the complete state graph search is \([0-9]*\).*/\1/p' "$log" | tail -1)
    rate=-
    if [ -n "$ds" ] && [ -n "$secs" ] && [ "$secs" -gt 0 ] 2>/dev/null; then
      rate=$(( $(echo "$ds" | tr -d ',') / secs ))
    fi
    ok=no; [ "$res" = "$expect" ] && ok=yes
    echo "| $name | $tier | $kind | $expect | $res | $ok | ${ds:--} | ${depth:--} | ${secs:--} | $rate |"
  done >> "$out"
  {
    echo
    n_run=$(awk -F'|' '$3 ~ /^ [SML] $/' "$out" | wc -l | tr -d ' ')
    n_bad=$(awk -F'|' '$3 ~ /^ [SML] $/ && $7 ~ /no/' "$out" | wc -l | tr -d ' ')
    for t in S M L; do
      s=$(awk -F'|' -v t=" $t " '$3 == t && $10 ~ /[0-9]/ {s += $10; c++} END {printf "%d configurations run, %d s in total", c, s}' "$out")
      echo "- Tier $t: $s"
    done
    echo
    if [ "$n_bad" = 0 ]; then echo "**ALL AS EXPECTED** ($n_run configurations)"
    else echo "**SURPRISES: $n_bad of $n_run** (the rows with As expected = no; read their logs)"; fi
  } >> "$out"
}

CHILD=""; CUR_LOG=""; CUR_START=0
on_stop() {
  trap - INT TERM HUP
  if [ -n "$CHILD" ]; then kill -TERM "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null; fi
  if [ -n "$CUR_LOG" ]; then
    { echo "RUNNER-SECONDS $(( $(date +%s) - CUR_START ))"; echo "RUNNER-VERDICT INCOMPLETE"; } >> "$CUR_LOG"
  fi
  rm -rf "$RES/states"
  summary
  echo
  echo "stopped; finished configurations are kept and skipped next time (results/SUMMARY.md)"
  exit 130
}
trap on_stop INT TERM HUP

grep -v '^#' "$LIST" > "$RES/.list"
while read -r name expect tier kind mode; do
  [ -n "$name" ] || continue
  selected "$name" "$expect" "$tier" "$kind" || continue
  cfg="$HERE/cfg/$name.cfg"
  log="$RES/$name.log"
  hash=$( (cat "$MODEL" "$cfg"; echo "$TOOLKEY") | sha256)
  if [ "${mode:-check}" = simulate ]; then
    EXTRA="-simulate num=$SIM_TRACES -depth $SIM_DEPTH"
  else
    EXTRA=""
  fi
  if [ "$FORCE" = 0 ] && [ -f "$log" ] && grep -q "^RUNNER-HASH $hash" "$log" \
     && grep -q "^RUNNER-VERDICT " "$log" && ! grep -q "^RUNNER-VERDICT INCOMPLETE" "$log"; then
    echo "skip   $name (done: $(sed -n 's/^RUNNER-VERDICT //p' "$log"))"
    continue
  fi
  printf 'run    %-36s tier %s  expect %s ... ' "$name" "$tier" "$expect"
  rm -rf "$RES/states/$name"
  start=$(date +%s); CUR_LOG="$log"; CUR_START=$start
  (cd "$HERE" && exec nice -n 10 java -XX:+UseParallelGC $MEM -cp "$JAR" tlc2.TLC \
      -config "cfg/$name.cfg" -workers "$WORKERS" -metadir "$RES/states/$name" -nowarning $EXTRA \
      EngineJobs.tla) < /dev/null > "$log" 2>&1 &
  CHILD=$!
  wait "$CHILD"
  CHILD=""; CUR_LOG=""
  secs=$(( $(date +%s) - start ))
  rm -rf "$RES/states/$name"
  v=$(verdict "$log" "$cfg" "${mode:-check}")
  { echo "RUNNER-HASH $hash"; echo "RUNNER-SECONDS $secs"; echo "RUNNER-VERDICT $v"; } >> "$log"
  mark="as expected"; [ "$v" = "$expect" ] || mark="SURPRISE"
  echo "$v in ${secs}s ($mark)"
  summary
done < "$RES/.list"
rm -f "$RES/.list"
rm -rf "$RES/states"
summary
tail -5 "$RES/SUMMARY.md"
