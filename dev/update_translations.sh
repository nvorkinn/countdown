#!/bin/sh
# Gathers every text the screen draws into locale/messages.pot, then brings each language's catalogue (locale/nb.po)
# in line with it: new texts arrive untranslated, removed ones are kept as obsolete (#~). Run from the repository root after
# adding or changing a text marked with _(), N_() or ngettext(); see core/src/countdown_core/i18n.py.
set -e
LOCALE=core/src/countdown_core/locale
uv run pybabel extract -F babel.cfg -k _ -k N_ -k ngettext:1,2 -c Translators: --no-location --sort-output \
  --project countdown --copyright-holder "Nikolai Vorkinn" -o "$LOCALE/messages.pot" .
for po in "$LOCALE"/*.po; do
  uv run pybabel update -i "$LOCALE/messages.pot" -o "$po" -l "$(basename "$po" .po)" --no-fuzzy-matching
done
