#!/bin/bash
# Publish a new version of MindChart. Installed copies pick it up the next time they start.
#   scripts/release.sh 0.2.0 "What changed, in a sentence or two"
set -e
cd "$(dirname "$0")/.."
VERSION="$1"
NOTES="$2"
if [ -z "$VERSION" ] || [ -z "$NOTES" ]; then
  echo "Usage: scripts/release.sh <version> \"<what changed>\""
  exit 1
fi

# Nothing is published unless every test passes on Apple's built-in Python.
echo "Running tests..."
if ! scripts/test.sh; then
  echo "Tests failed, so nothing was published."
  exit 1
fi

echo "$VERSION" > VERSION

# The PDF reader and the Mac app are built here (they're compiled programs, so they aren't kept in git),
# for macOS 12 and newer, not just this Mac's version.
swiftc -O -target arm64-apple-macos12.0 tools/pdftext.swift -o tools/pdftext
swiftc -O -target arm64-apple-macos12.0 tools/touchid.swift -o tools/touchid
scripts/build_app.sh
for program in tools/pdftext tools/touchid MindChart.app/Contents/MacOS/MindChart; do
  if [ "$(otool -l "$program" | awk '/minos/ { print $2; exit }')" != "12.0" ]; then
    echo "$program would only run on a newer macOS than 12, so nothing was published."
    exit 1
  fi
done

# Everything git tracks, minus files only needed for development, plus the built PDF reader and Touch ID helper.
git add -A
FILES=$(git ls-files | grep -v -e '^scripts/' -e '^tests/' -e '^\.github/' -e '^install.sh$' -e '^\.gitignore$')
mkdir -p dist
APP_FILES=$(find MindChart.app -type f)
printf '%s\n' $FILES tools/pdftext tools/touchid $APP_FILES MANIFEST | sort > MANIFEST
rm -f dist/MindChart.zip
# shellcheck disable=SC2086
echo "$FILES" | tr '\n' '\0' | xargs -0 zip -q dist/MindChart.zip tools/pdftext tools/touchid MANIFEST
zip -q -r dist/MindChart.zip MindChart.app
(cd dist && shasum -a 256 MindChart.zip > MindChart.zip.sha256)
rm MANIFEST
# The built app is in the zip now; left here it would show up in Launchpad as a second MindChart
rm -rf MindChart.app

git commit -m "Release $VERSION

$NOTES

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git tag "v$VERSION"
git push origin HEAD --tags
gh release create "v$VERSION" dist/MindChart.zip dist/MindChart.zip.sha256 --title "MindChart $VERSION" --notes "$NOTES"
echo "Published MindChart $VERSION."
