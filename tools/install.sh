#!/bin/sh
set -eu

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Usage: tools/install.sh' 'Build and install only; services/backends are not started or stopped.' >&2
    exit 2
fi
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'The supported installation target is Linux.' >&2
    exit 2
fi

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
cargo test --locked --workspace
./build.sh
test -x target/release/herdr-web

bin_directory="$HOME/.local/bin"
state_directory="$HOME/.local/state/herdr-web"
umask 077
mkdir -p "$bin_directory" "$state_directory"
if [ -L "$state_directory" ] || [ "$(stat -c %u "$state_directory")" != "$(id -u)" ]; then
    printf '%s\n' 'Refusing an unowned or symlinked installation state directory.' >&2
    exit 1
fi
chmod 700 "$state_directory"
if [ -e "$bin_directory/herdr-web" ]; then
    backup=$(mktemp -d "$state_directory/rollback.XXXXXXXX")
    cp -pL "$bin_directory/herdr-web" "$backup/herdr-web"
    ps -u "$(id -u)" -o pid,ppid,args > "$backup/processes.txt"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user show herdr-web.service > "$backup/user-service.txt" 2>&1 || true
    fi
    printf 'Rollback binary and launch inventory: %s\n' "$backup"
fi

temporary_binary=$(mktemp "$bin_directory/.herdr-web.new.XXXXXXXX")
trap 'rm -f "$temporary_binary"' EXIT HUP INT TERM
install -m 755 target/release/herdr-web "$temporary_binary"
mv -f "$temporary_binary" "$bin_directory/herdr-web"
printf 'Installed: %s/herdr-web\n' "$bin_directory"
printf '%s\n' 'No backend or service was started/stopped.' \
    'Choose the intended session, then follow README.md standalone or native-plugin instructions.' \
    'Check listeners before starting; never stop an unrelated owner of port 8000.'
