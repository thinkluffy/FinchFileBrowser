#!/bin/sh
# Run Finch's GUI tests without touching the desktop they are started from.
#
#   tests/run.sh                 every tests/test_*.py
#   tests/run.sh pathbar grid    tests/test_pathbar.py and tests/test_grid.py
#   tests/run.sh -v network      also print the test's whole output
#   tests/run.sh -- CMD ARGS…    run any command in the same environment
#
# Each test gets a fresh environment, thrown away afterwards:
#   - a nested X server (Xephyr), whose window is minimised at once so that a
#     stray click of yours cannot land in the application under test;
#   - its own D-Bus session, so gvfs (recent files, trash, mounts) is private;
#   - its own config, data and runtime directories in ~/finch-sandbox.XXXX
#     ($FINCH_SANDBOX; in the home folder so that trashing a file there stays
#     on one filesystem). Nothing reads or writes ~/.config/finch, the real
#     recently-used list or the real trash.
# The system bus is NOT private: real drives are visible to the tests, which
# therefore never open or mount one.
#
# Needs Xephyr, xdotool, dbus-run-session and python3-gi; some tests need 7z.
# FINCH_TEST_DISPLAY (default :7) and FINCH_TEST_TIMEOUT (seconds per test,
# default 150) can be set in the environment.
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
NESTED=${FINCH_TEST_DISPLAY:-:7}
LIMIT=${FINCH_TEST_TIMEOUT:-150}
HOST=${DISPLAY:-:0}

for tool in Xephyr xdotool xdpyinfo dbus-run-session python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "tests/run.sh: $tool is not installed" >&2; exit 2; }
done

XEPHYR_PID=
SANDBOX=
cleanup() {
    [ -n "$SANDBOX" ] && rm -rf "$SANDBOX"
    if [ -n "$XEPHYR_PID" ]; then
        kill "$XEPHYR_PID" 2>/dev/null
        wait "$XEPHYR_PID" 2>/dev/null
    fi
    XEPHYR_PID=
    SANDBOX=
}
trap 'cleanup; exit 130' INT TERM

# in_sandbox LOGFILE CMD ARGS…: run the command in a fresh environment.
in_sandbox() {
    log=$1; shift
    DISPLAY=$HOST Xephyr "$NESTED" -screen 1200x900 -nolisten tcp >/dev/null 2>&1 &
    XEPHYR_PID=$!
    tries=0
    until DISPLAY=$NESTED xdpyinfo >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [ $tries -gt 50 ]; then
            echo "tests/run.sh: Xephyr $NESTED did not start" >&2
            cleanup; return 99
        fi
        sleep 0.1
    done
    DISPLAY=$HOST xdotool search --name "Xephyr on $NESTED" 2>/dev/null | while read -r win; do
        DISPLAY=$HOST xdotool windowminimize "$win"
    done
    SANDBOX=$(mktemp -d "$HOME/finch-sandbox.XXXX")
    mkdir -p "$SANDBOX/config" "$SANDBOX/data" "$SANDBOX/run" "$SANDBOX/files" "$SANDBOX/tmp"
    chmod 700 "$SANDBOX/run"
    (
        cd "$ROOT/src/Finch" || exit 99
        export DISPLAY="$NESTED" LANGUAGE="${FINCH_TEST_LANGUAGE:-C}" LANG="${FINCH_TEST_LANG:-C.UTF-8}"
        export FINCH_SANDBOX="$SANDBOX" XDG_CONFIG_HOME="$SANDBOX/config" XDG_DATA_HOME="$SANDBOX/data"
        export XDG_RUNTIME_DIR="$SANDBOX/run" GVFS_DISABLE_FUSE=1
        export TMPDIR="$SANDBOX/tmp"    # what tempfile.mkdtemp() makes goes with the sandbox
        # The limit is in here, so that the clean-up always runs.
        if [ -n "$log" ]; then
            timeout "$LIMIT" dbus-run-session -- "$@" >"$log" 2>&1
        else
            timeout "$LIMIT" dbus-run-session -- "$@"
        fi
    )
    rc=$?
    sleep 1                 # let the session's daemons go before their files do
    cleanup
    sleep 1                 # ...and the X server, before the next one takes its number
    return $rc
}

VERBOSE=
if [ "${1:-}" = "-v" ]; then VERBOSE=1; shift; fi
if [ "${1:-}" = "--" ]; then
    shift
    in_sandbox "" "$@"
    exit $?
fi

if [ $# -eq 0 ]; then
    set -- "$HERE"/test_*.py
fi
LOG=$(mktemp /tmp/finch-test-log.XXXX)
failed=0; total=0
for t in "$@"; do
    case $t in
        */*) file=$t ;;
        test_*) file=$HERE/${t%.py}.py ;;
        *) file=$HERE/test_${t%.py}.py ;;
    esac
    name=$(basename "$file" .py)
    if [ ! -e "$file" ]; then echo "$name: no such test"; failed=$((failed + 1)); continue; fi
    total=$((total + 1))
    in_sandbox "$LOG" python3 "$file"
    rc=$?
    passes=$(grep -c '^PASS' "$LOG")
    fails=$(grep -c '^FAIL\( \|$\)' "$LOG")
    crashed=$(grep -c '^Traceback' "$LOG")
    if [ $rc -eq 0 ] && [ "$fails" -eq 0 ] && [ "$crashed" -eq 0 ]; then
        printf 'ok    %-22s %3d checks\n' "$name" "$passes"
    else
        failed=$((failed + 1))
        why="$fails failed"
        [ "$crashed" -gt 0 ] && why="$why, exception"
        [ $rc -eq 124 ] && why="$why, timed out after ${LIMIT}s"
        printf 'FAIL  %-22s %3d checks passed, %s (exit %d)\n' "$name" "$passes" "$why" "$rc"
        [ -z "$VERBOSE" ] && grep -A12 '^FAIL\( \|$\)\|^Traceback' "$LOG" | cut -c1-240 | head -40 | sed 's/^/      /'
    fi
    [ -n "$VERBOSE" ] && sed 's/^/      /' "$LOG"
done
rm -f "$LOG"
echo "$((total - failed)) of $total tests passed"
[ $failed -eq 0 ]
