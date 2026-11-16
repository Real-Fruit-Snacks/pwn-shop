#!/bin/sh
set -eu

# Downloads the published index and the zips it names into a folder, checking each
# zip's SHA-256 against the index. Everything runs from main, called on the last line.

usage_line() {
    echo "usage: fetch.sh <folder>"
}

usage_full() {
    usage_line
    cat <<'HELP'

Downloads index.toml from the index release of pwn-shop, then every zip it names from
that zip's own release, into <folder> (created if it is missing). Each zip is checked
against the SHA-256 in the index before it is kept; one that fails is removed and the
script stops. A zip that is already there with the right hash is not downloaded again.
A link or a folder where a zip or the index would go is refused, and so is a download
that stalls for a minute.

Needs curl and sha256sum, and no account: the files are downloaded from the
repository's public releases. PWN_SHOP_REPO sets the repository (default
Real-Fruit-Snacks/pwn-shop); it has to be public.

It changes nothing else on this machine. At the end it prints the line to put in
/opt/pwn-broker/config.toml.
HELP
}

ok() {
    printf 'ok    %s\n' "$1"
}

fail() {
    printf 'fail  %s\n' "$1" >&2
    exit 1
}

need() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is not installed. $2"
}

# Prints one line per build in the index: tag, file name and SHA-256, separated by tabs.
#
# The index is the file build.py writes. Every value is on one line and every string is
# escaped, so a description cannot hold a line break and so cannot start a line of its
# own. Only lines that are exactly `key = "value"` count, and only the keys of a
# [[tool.build]] table are read for file, sha256, version and build; the tool's name is
# read from the [[tool]] table above it. A description that mentions "file" or "sha256"
# sits in the [[tool]] table, on a line that starts with `description`, and is never read.
#
# build.py writes two table headers, flush left, and no comments. Any other line whose
# first character after spaces or tabs is a bracket is refused: read as some other table, the build under it would be left out
# while the index still named its zip. An index with a problem prints no line at all.
parse_index() {
    # the $ in the patterns below are regular-expression anchors for awk
    # shellcheck disable=SC2016
    awk -v index_file="$1" '
    BEGIN {
        bad = 0; n = 0; inbuild = 0
        name = ""; file = ""; sha = ""; ver = ""; bld = ""
        while ((getline line < index_file) > 0) {
            if (line == "[[tool]]") { emit(); inbuild = 0; name = ""; continue }
            if (line == "[[tool.build]]") { emit(); inbuild = 1; continue }
            if (line ~ /^[ \t]*\[/) { emit(); inbuild = 0; problem("the index has a table header build.py does not write"); continue }
            if (!inbuild) {
                if (line ~ /^name = "[^"]*"$/) name = value(line)
                continue
            }
            if (line ~ /^version = "[^"]*"$/) ver = value(line)
            else if (line ~ /^build = [0-9]+$/) { bld = line; sub(/^build = /, "", bld) }
            else if (line ~ /^file = "[^"]*"$/) file = value(line)
            else if (line ~ /^sha256 = "[^"]*"$/) sha = value(line)
        }
        emit()
        if (n == 0 && !bad) problem("the index names no packages")
        if (!bad) for (i = 0; i < n; i++) print out[i]
        exit bad
    }
    function value(line,   v) {
        v = line
        sub(/^[a-z0-9_]+ = "/, "", v)
        sub(/"$/, "", v)
        return v
    }
    function problem(text) {
        print "fetch: " text > "/dev/stderr"
        bad = 1
    }
    function emit() {
        if (!inbuild) return
        # the name rule of build.py and pwn-broker: a dot only between other characters
        if (name !~ /^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)*$/) problem("a build has no valid tool name")
        else if (ver !~ /^[A-Za-z0-9][A-Za-z0-9._+~-]*$/) problem(name ": a build has no valid version")
        else if (bld == "" || bld + 0 < 1) problem(name ": a build has no build number")
        else if (file != name "-" ver "-" bld ".zip") problem(name ": file " file " is not " name "-" ver "-" bld ".zip")
        else if (length(sha) != 64 || sha !~ /^[0-9A-Fa-f]+$/) problem(file ": the SHA-256 is not 64 hexadecimal characters")
        else { out[n] = name "-v" ver "-" bld "\t" file "\t" tolower(sha); n++ }
        ver = ""; bld = ""; file = ""; sha = ""
    }
    '
}

# The file is read on standard input, so sha256sum never prints its path: it marks the
# line of a path that holds a line break, and the hash would no longer compare.
hash_of() {
    sha256sum <"$1" | cut -d' ' -f1 | tr 'A-F' 'a-f'
}

# download <tag> <file> <destination>: one file of one release, over HTTPS only. It gives
# up when no connection is made in 30 seconds or nothing arrives for a minute.
download() {
    curl -fsSL --proto '=https' --proto-redir '=https' \
        --connect-timeout 30 --speed-limit 1 --speed-time 60 -o "$3" \
        "https://github.com/$REPO/releases/download/$1/$2" </dev/null
}

# A link or a folder where a file is about to go: mv would put the file inside it, which
# for a link is outside the folder this script was given.
in_the_way() {
    if [ -L "$1" ] || [ -d "$1" ]; then
        fail "$1 is a link or a folder. move it away, then run this again"
    fi
}

# What config.toml could not hold between quotes without escaping.
unsafe_path() {
    case "$1" in
        *"$QUOTE"* | *"$BACKSLASH"* | *[[:cntrl:]]*) return 0 ;;
    esac
    return 1
}

cleanup() {
    if [ -n "$WORK" ]; then rm -rf "$WORK"; fi
    # a folder this run made and put nothing in is not left behind
    if [ "$CREATED" -eq 1 ]; then rmdir -- "$FOLDER" 2>/dev/null || true; fi
}

main() {
    REPO="${PWN_SHOP_REPO:-Real-Fruit-Snacks/pwn-shop}"

    case "${1:-}" in
        --help | -h)
            usage_full
            exit 0
            ;;
        --parse-index)
            # for the tests: print what would be fetched from an index file, and nothing else
            if [ "$#" -ne 2 ]; then usage_line >&2; exit 2; fi
            parse_index "$2"
            exit 0
            ;;
    esac
    if [ "$#" -ne 1 ] || [ -z "$1" ]; then usage_line >&2; exit 2; fi

    need curl "On Kali: apt install curl."
    need sha256sum "It is part of coreutils."
    case "$REPO" in
        */*/* | /* | */ | *..* | *[!A-Za-z0-9._/-]*) fail "PWN_SHOP_REPO must look like owner/name, not $REPO" ;;
        */*) ;;
        *) fail "PWN_SHOP_REPO must look like owner/name, not $REPO" ;;
    esac

    QUOTE='"'
    BACKSLASH="$(printf '\134')"
    UNSAFE="holds a quote, a backslash or a control character, which config.toml would need escaped"
    GIVEN="$1"
    # cd - is the folder you were in before, not a folder called -
    if [ "$GIVEN" = "-" ]; then GIVEN="./-"; fi
    if unsafe_path "$GIVEN"; then fail "the folder path $UNSAFE: $GIVEN"; fi

    NO_INDEX="cannot download index.toml from the index release of $REPO. check the network and that the repository is public. if a new index is being published, try again in a minute"
    # asked for once here, so a repository that cannot be read leaves no folder behind
    download index index.toml /dev/null || fail "$NO_INDEX"

    WORK=""
    FOLDER="$GIVEN"
    if [ -d "$GIVEN" ]; then CREATED=0; else CREATED=1; fi
    mkdir -p -- "$GIVEN" || fail "cannot create $GIVEN"
    trap cleanup EXIT
    trap 'exit 1' HUP INT TERM
    # CDPATH would send a relative name to another folder and make cd print it. The dot is
    # added and taken off again because $( ) drops line breaks at the end: a path that ends
    # in one would come back as the folder beside it
    RESOLVED="$(CDPATH='' cd -- "$GIVEN" && pwd -P && echo .)" || fail "cannot enter $GIVEN"
    RESOLVED="${RESOLVED%.}"
    FOLDER="${RESOLVED%?}"
    # the name given was plain, but a folder above it may not be
    if unsafe_path "$FOLDER"; then fail "the folder path $UNSAFE: $FOLDER"; fi
    in_the_way "$FOLDER/index.toml"

    # work folders left by an earlier run that was killed
    for OLD in "$FOLDER"/.fetch.??????; do
        if [ -d "$OLD" ] && [ ! -L "$OLD" ]; then rm -rf -- "$OLD"; fi
    done

    WORK="$(mktemp -d "$FOLDER/.fetch.XXXXXX")" || fail "cannot write to $FOLDER"

    download index index.toml "$WORK/index.toml" || fail "$NO_INDEX"
    [ -s "$WORK/index.toml" ] || fail "the index release of $REPO has no index.toml"
    parse_index "$WORK/index.toml" >"$WORK/plan" || fail "the index is not in the form build.py writes; nothing was fetched"

    TAB="$(printf '\t')"
    COUNT=0
    while IFS="$TAB" read -r TAG FILE SHA; do
        DEST="$FOLDER/$FILE"
        in_the_way "$DEST"
        if [ -f "$DEST" ] && [ "$(hash_of "$DEST")" = "$SHA" ]; then
            ok "$FILE is already here"
            COUNT=$((COUNT + 1))
            continue
        fi
        if ! download "$TAG" "$FILE" "$WORK/$FILE"; then
            rm -f "$WORK/$FILE"
            fail "cannot download $FILE from the release $TAG"
        fi
        if [ ! -f "$WORK/$FILE" ] || [ "$(hash_of "$WORK/$FILE")" != "$SHA" ]; then
            # only the file just downloaded; a copy already in the folder is left as it was
            rm -f "$WORK/$FILE"
            KEPT=""
            if [ -e "$DEST" ]; then KEPT=". the copy already in the folder was left as it was"; fi
            fail "the download of $FILE does not match the SHA-256 in the index and was discarded$KEPT"
        fi
        mv -f "$WORK/$FILE" "$DEST"
        ok "$FILE downloaded and checked"
        COUNT=$((COUNT + 1))
    done <"$WORK/plan"

    # last, so the folder never holds an index that names a zip which was not checked
    mv -f "$WORK/index.toml" "$FOLDER/index.toml"
    ok "$COUNT packages in $FOLDER"
    echo "put this line in /opt/pwn-broker/config.toml:"
    printf 'tools_index = "%s"\n' "$FOLDER"
}

main "$@"
