#!/bin/sh
# Build Finch into output/:
#   output/finch          single-file executable (Python zipapp: code + translations)
#   output/finch.desktop  application menu entry pointing at output/finch
set -e
cd "$(dirname "$0")"
ROOT=$(pwd)
sh src/Finch/po/update.sh
rm -rf output/build output/finch output/finch.desktop
mkdir -p output/build
cp -r src/Finch/app output/build/app
find output/build -name __pycache__ -prune -exec rm -rf {} +
python3 -m zipapp output/build --main app.application:main \
    --python "/usr/bin/env python3" --compress --output output/finch
rm -rf output/build
sed "s|^Exec=.*|Exec=$ROOT/output/finch %f|" src/Finch/finch.desktop > output/finch.desktop
ls -l output
