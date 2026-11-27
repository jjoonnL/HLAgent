#!/bin/sh
set -eu

apt-get update
apt-get install -y --no-install-recommends plink1.9=1.90~b6.26-220402-1 mawk r-base-core default-jre-headless ca-certificates curl unzip
ln -sf /usr/bin/plink1.9 /usr/local/bin/plink
test "$(uname -m)" = x86_64 || { echo 'This HATK image requires Linux x86_64.' >&2; exit 1; }
curl --fail --location --retry 2 \
    https://s3.amazonaws.com/plink2-assets/beta1/plink2_linux_x86_64_20261031.zip \
    -o /tmp/plink2.zip
echo 'b0f4ff5537045576c287b15cbda3eb22ab8f218833cfa198df7b635c85d46a47  /tmp/plink2.zip' | sha256sum -c -
unzip -j /tmp/plink2.zip plink2 -d /usr/local/bin
chmod 755 /usr/local/bin/plink2
rm /tmp/plink2.zip
mkdir -p /opt/hlagent/dependency
curl --fail --location --retry 2 \
    https://faculty.washington.edu/browning/beagle/beagle.jar \
    -o /opt/hlagent/dependency/beagle.jar
echo 'c20bb952626a06cae044062de37a036a1a8bb23bb395105a16eb764c64a88ff0  /opt/hlagent/dependency/beagle.jar' | sha256sum -c -
rm -rf /var/lib/apt/lists/*
python -m pip install --no-cache-dir --only-binary=:all: -r /opt/hlagent/requirements.txt
python -m pip check
python -m hlagent.analysis.hatk_worker --check
mkdir -p /work
