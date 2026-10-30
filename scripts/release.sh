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

echo "$VERSION" > VERSION

# The PDF reader is built here (it's a compiled program, so it isn't kept in git).
swiftc -O tools/pdftext.swift -o tools/pdftext

# Everything git tracks, minus files only needed for development, plus the built PDF reader.
git add -A
FILES=$(git ls-files | grep -v -e '^scripts/' -e '^install.sh$' -e '^\.gitignore$')
mkdir -p dist
printf '%s\n' $FILES tools/pdftext MANIFEST | sort > MANIFEST
rm -f dist/MindChart.zip
# shellcheck disable=SC2086
echo "$FILES" | tr '\n' '\0' | xargs -0 zip -q dist/MindChart.zip tools/pdftext MANIFEST
(cd dist && shasum -a 256 MindChart.zip > MindChart.zip.sha256)
rm MANIFEST

git commit -m "Release $VERSION

$NOTES

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git tag "v$VERSION"
git push origin HEAD --tags
gh release create "v$VERSION" dist/MindChart.zip dist/MindChart.zip.sha256 --title "MindChart $VERSION" --notes "$NOTES"
echo "Published MindChart $VERSION."
