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
            if (line ~ /^\[/) { emit(); inbuild = 0; continue }
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
        else { printf "%s-v%s-%s\t%s\t%s\n", name, ver, bld, file, tolower(sha); n++ }
        ver = ""; bld = ""; file = ""; sha = ""
    }
    '
}

hash_of() {
    sha256sum "$1" | cut -d' ' -f1 | tr 'A-F' 'a-f'
}

# download <tag> <file> <destination>: one file of one release, over HTTPS only
download() {
    curl -fsSL --proto '=https' --proto-redir '=https' -o "$3" \
        "https://github.com/$REPO/releases/download/$1/$2" </dev/null
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
        */*) ;;
        *) fail "PWN_SHOP_REPO must look like owner/name, not $REPO" ;;
    esac
    case "$REPO" in
        *[!A-Za-z0-9._/-]*) fail "PWN_SHOP_REPO must look like owner/name, not $REPO" ;;
    esac
    NO_INDEX="cannot download index.toml from the index release of $REPO. check the network, and that the repository is public"
    # asked for once here, so a repository that cannot be read leaves no folder behind
    download index index.toml /dev/null || fail "$NO_INDEX"

    mkdir -p -- "$1" || fail "cannot create $1"
    FOLDER="$(cd -- "$1" && pwd -P)" || fail "cannot enter $1"
    QUOTE='"'
    BACKSLASH="$(printf '\134')"
    case "$FOLDER" in
        *"$QUOTE"* | *"$BACKSLASH"*) fail "the folder path holds a quote or a backslash, which config.toml would need escaped: $FOLDER" ;;
    esac

    # work folders left by an earlier run that was killed
    for OLD in "$FOLDER"/.fetch.??????; do
        if [ -d "$OLD" ] && [ ! -L "$OLD" ]; then rm -rf -- "$OLD"; fi
    done

    WORK="$(mktemp -d "$FOLDER/.fetch.XXXXXX")" || fail "cannot write to $FOLDER"
    trap 'rm -rf "$WORK"' EXIT
    trap 'exit 1' HUP INT TERM

    download index index.toml "$WORK/index.toml" || fail "$NO_INDEX"
    [ -s "$WORK/index.toml" ] || fail "the index release of $REPO has no index.toml"
    parse_index "$WORK/index.toml" >"$WORK/plan" || fail "the index is not in the form build.py writes; nothing was fetched"

    TAB="$(printf '\t')"
    COUNT=0
    while IFS="$TAB" read -r TAG FILE SHA; do
        DEST="$FOLDER/$FILE"
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
            fail "$FILE does not match the SHA-256 in the index, so it was removed"
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
