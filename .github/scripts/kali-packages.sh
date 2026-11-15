#!/bin/sh
# The packages in Kali, inside kalilinux/kali-rolling as root. CI starts the container with the
# built packages and their index at /packages, this repository at /shop and pwn-broker at
# /pwn-broker, all read-only, and an evidence folder at /evidence; then runs this twice:
#
#   kali-packages.sh prepare   with the network on: Kali's python3 and a virtual environment
#                              holding pwn-broker, installed from its checkout
#   kali-packages.sh install   with the network off: install-check.py installs every package
#                              through pwn-broker's own code and starts each command
#
# Everything worth reading afterwards goes into /evidence, whatever happens.

set -eu

EVIDENCE=/evidence
WORK=/work
BROKER="$WORK/broker"
PACKAGES="python3 python3-venv ca-certificates"

export HOME=/root
export DEBIAN_FRONTEND=noninteractive
mkdir -p "$EVIDENCE"
trap 'chmod -R a+rX "$EVIDENCE"' EXIT

say() {
    printf '\n==== %s\n' "$1"
}

stop() {
    printf 'fail  %s\n' "$1"
    exit 1
}

prepare_kali() {
    say "packages"
    if ! apt-get update >"$EVIDENCE/apt.txt" 2>&1; then
        tail -n 40 "$EVIDENCE/apt.txt"
        stop "apt-get update failed"
    fi
    # shellcheck disable=SC2086
    if ! apt-get install -y --no-install-recommends $PACKAGES >>"$EVIDENCE/apt.txt" 2>&1; then
        tail -n 40 "$EVIDENCE/apt.txt"
        stop "the packages could not be installed"
    fi

    say "pwn-broker"
    mkdir -p "$WORK"
    rm -rf "$WORK/pwn-broker"
    # a copy, so building it writes nothing into the read-only checkout
    cp -R /pwn-broker "$WORK/pwn-broker" || stop "cannot copy /pwn-broker to $WORK/pwn-broker"
    log="$EVIDENCE/broker-install.txt"
    if ! python3 -m venv "$BROKER" >"$log" 2>&1 \
        || ! "$BROKER/bin/python" -m pip install "$WORK/pwn-broker" >>"$log" 2>&1; then
        tail -n 40 "$log"
        stop "pwn-broker could not be installed"
    fi

    say "versions"
    {
        cat /etc/os-release
        echo
        echo "\$ python3 --version"
        python3 --version 2>&1
        echo
        echo "\$ $BROKER/bin/python -m pip --version"
        "$BROKER/bin/python" -m pip --version 2>&1
        echo
        echo "\$ $BROKER/bin/python -m pip show pwn-broker"
        "$BROKER/bin/python" -m pip show pwn-broker 2>&1
        echo
        # the format is dpkg-query's own, not the shell's
        # shellcheck disable=SC2016,SC2086
        dpkg-query -W -f '${Package} ${Version}\n' $PACKAGES
    } >"$EVIDENCE/versions.txt" 2>&1
    cat "$EVIDENCE/versions.txt"
}

install_packages() {
    [ -x "$BROKER/bin/python" ] || stop "$BROKER has no python; run prepare first"
    say "the packages, with the network off"
    ls -l /packages >"$EVIDENCE/packages.txt" 2>&1
    cat /packages/index.toml >>"$EVIDENCE/packages.txt" 2>&1
    if "$BROKER/bin/python" /shop/.github/scripts/install-check.py /packages --network-off \
        >"$EVIDENCE/install-check.txt" 2>&1; then
        status=0
    else
        status=$?
    fi
    cat "$EVIDENCE/install-check.txt"
    [ "$status" -eq 0 ] || stop "install-check.py exited with status $status"
    echo "ok    every package installed and started in Kali with the network off"
}

case "${1:-}" in
    prepare) prepare_kali ;;
    install) install_packages ;;
    *)
        echo "usage: kali-packages.sh prepare|install" >&2
        exit 2
        ;;
esac
