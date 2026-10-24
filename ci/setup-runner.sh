#!/usr/bin/env bash
# Reviewed local dependency list; never execute a fetched setup script as root.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'This builder requires Linux.' >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || {
    echo 'Use the ubuntu-24.04 runner.' >&2; exit 1;
}
# Only ephemeral GitHub-hosted machines may discard preinstalled, unused SDKs.
# Preserve runner/node/gh and caches; never apply this cleanup to a local host.
if [[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]]; then
    sudo rm -rf -- /usr/local/lib/android /usr/share/dotnet /opt/ghc \
        /usr/local/.ghcup /usr/share/swift
fi
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ack antlr3 asciidoc autoconf automake autopoint bc binutils bison build-essential \
    ccache clang cmake cpio curl device-tree-compiler dos2unix fastjar flex g++ \
    fakeroot file gawk gcc-multilib gettext git gperf help2man intltool jq libelf-dev libfuse-dev \
    libglib2.0-dev libgmp3-dev libltdl-dev libmpc-dev libmpfr-dev libncurses-dev \
    libpython3-dev libreadline-dev libssl-dev libtool libyaml-dev lld llvm \
    meson nano ninja-build p7zip-full patch pkgconf python3 python3-pip \
    python3-ply python3-setuptools python3-venv qemu-utils rsync squashfs-tools \
    subversion swig texinfo time u-boot-tools unzip wget xsltproc zlib1g-dev zstd
available_kb=$(df -Pk "$PWD" | awk 'NR==2 {print $4}')
minimum_kb=${S20P_MIN_FREE_KB:-52428800}
if (( available_kb < minimum_kb )); then
    echo "At least $minimum_kb KiB free required; available $available_kb KiB." >&2
    exit 1
fi
mkdir -p output/provenance
dpkg-query -W -f='${Package}\t${Version}\n' > output/provenance/runner-packages.tsv
cp /etc/os-release output/provenance/runner-os-release
df -h "$PWD"
