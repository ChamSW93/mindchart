#!/bin/bash
# Build MindChart.app: the Mac app with its icon.
#   scripts/build_app.sh             builds MindChart.app here, for releases (runs the installed copy of the code)
#   scripts/build_app.sh --dev       builds it into ~/Applications, running this working copy (for development)
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ "$1" = "--dev" ]; then APP="$HOME/Applications/MindChart.app"; HOME_KEY="<key>MindChartHome</key><string>$ROOT</string>"
else APP="$ROOT/MindChart.app"; HOME_KEY=""; fi

echo "Drawing the icon..."
swiftc -O tools/make_icon.swift -o "$WORK/make_icon" 2> /dev/null
"$WORK/make_icon" "$WORK/icon.png"
mkdir "$WORK/AppIcon.iconset"
for size in 16 32 128 256 512; do
  sips -z $size $size "$WORK/icon.png" --out "$WORK/AppIcon.iconset/icon_${size}x${size}.png" > /dev/null
  sips -z $((size * 2)) $((size * 2)) "$WORK/icon.png" --out "$WORK/AppIcon.iconset/icon_${size}x${size}@2x.png" > /dev/null
done
iconutil -c icns "$WORK/AppIcon.iconset" -o "$WORK/AppIcon.icns"
sips -z 128 128 "$WORK/icon.png" --out static/icon.png > /dev/null  # also used as the web page's icon

echo "Building the app..."
swiftc -O tools/MindChartApp.swift -o "$WORK/MindChart" -framework Cocoa -framework WebKit

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$WORK/MindChart" "$APP/Contents/MacOS/MindChart"
cp "$WORK/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>MindChart</string>
  <key>CFBundleDisplayName</key><string>MindChart</string>
  <key>CFBundleIdentifier</key><string>io.github.chamsw93.mindchart</string>
  <key>CFBundleExecutable</key><string>MindChart</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(cat VERSION)</string>
  <key>CFBundleVersion</key><string>$(cat VERSION)</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.medical</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  $HOME_KEY
</dict></plist>
EOF
codesign --force --deep --sign - "$APP" 2> /dev/null  # a local signature, which Apple Silicon Macs require to run it
touch "$APP"  # so Finder and the Dock pick up the new icon
echo "Built $APP"
