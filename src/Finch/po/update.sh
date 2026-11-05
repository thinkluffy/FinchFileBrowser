#!/bin/sh
# Regenerate finch.pot from the source, merge it into every *.po and compile
# the catalogues into ../app/locale/<lang>/LC_MESSAGES/finch.mo, where the
# program (and build.sh) picks them up.
set -e
cd "$(dirname "$0")"
OUT=../app/locale
xgettext --language=Python --from-code=UTF-8 \
    --keyword=_ --keyword=ngettext:1,2 --add-comments=TRANSLATORS \
    --package-name=finch --copyright-holder="Yuanbin Cai" -o finch.pot ../app/*.py
for po in *.po; do
    [ -e "$po" ] || continue
    lang=${po%.po}
    msgmerge --quiet --update --backup=none "$po" finch.pot
    mkdir -p "$OUT/$lang/LC_MESSAGES"
    msgfmt --check --statistics -o "$OUT/$lang/LC_MESSAGES/finch.mo" "$po"
done
