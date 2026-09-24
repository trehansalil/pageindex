#!/usr/bin/env bash
# scripts/lib/with-timeout.sh — portable `timeout SECS CMD [ARGS...]`.
#
# GNU `timeout` is not on macOS, and every non-systemd fallback in this repo
# (make test-uncapped, make gates, the test-budget gate, env_profile.sh) used
# to die with `exec: timeout: not found`. Dropping the timeout is NOT the fix:
# an unbounded pytest is what took this host down on 2026-09-17 (see the
# Makefile banner above `make test`).
#
# Resolution order: `timeout` (Linux) -> `gtimeout` (Homebrew coreutils) ->
# a perl watchdog (perl ships with macOS). All three exit 124 on expiry, so
# callers can keep checking for 124.
#
# The perl fallback runs the command in its own process group and signals the
# whole group — `uv run` spawns pytest as a child, and killing only uv would
# orphan the process that actually holds the memory.

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: with-timeout.sh SECONDS COMMAND [ARGS...]" >&2
    exit 125
fi

if command -v timeout >/dev/null 2>&1; then
    exec timeout "$@"
fi
if command -v gtimeout >/dev/null 2>&1; then
    exec gtimeout "$@"
fi
if ! command -v perl >/dev/null 2>&1; then
    echo "with-timeout.sh: none of timeout, gtimeout or perl found" >&2
    echo "                 (macOS: brew install coreutils)" >&2
    exit 125
fi

exec perl -e '
    use POSIX ();
    my $secs = shift @ARGV;
    die "with-timeout.sh: bad duration \"$secs\"\n" unless $secs =~ /^\d+$/;
    my $pid = fork // die "with-timeout.sh: fork: $!\n";
    if ($pid == 0) {
        setpgrp(0, 0);
        exec { $ARGV[0] } @ARGV
            or do { print STDERR "with-timeout.sh: exec $ARGV[0]: $!\n"; POSIX::_exit(127) };
    }
    setpgrp($pid, $pid);  # close the race where the alarm beats the child
    my $fwd = sub { kill $_[0], -$pid };
    $SIG{INT} = $SIG{TERM} = $SIG{HUP} = $fwd;
    $SIG{ALRM} = sub {
        kill "TERM", -$pid;
        for (1 .. 5) { last if waitpid($pid, POSIX::WNOHANG()) > 0; sleep 1 }
        kill "KILL", -$pid;
        waitpid($pid, 0);
        exit 124;
    };
    alarm $secs;
    my $r;
    do { $r = waitpid($pid, 0) } while ($r == -1 && $!{EINTR});
    exit(($? & 127) ? 128 + ($? & 127) : $? >> 8);
' "$@"
