#!/bin/bash
# MindChart installer. To install, paste this line into Terminal and press Return:
#   curl -fsSL https://raw.githubusercontent.com/ChamSW93/mindchart/main/install.sh | bash
# Running it again is safe: it picks up where it left off and updates an existing install.
set -e
REPOSITORY="ChamSW93/mindchart"
DEST="$HOME/Applications/MindChart"

echo ""
echo "Installing MindChart..."
echo ""

# 1. The Mac: Apple Silicon is needed to run the AI model at a usable speed.
if [ "$(uname -m)" != "arm64" ]; then
  echo "Sorry, MindChart needs a Mac with Apple Silicon (M1 or newer)."
  exit 1
fi
MEMORY_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "This Mac: $(sysctl -n machdep.cpu.brand_string), ${MEMORY_GB} GB memory"

# 2. Apple's free command line tools, which include Python.
if ! xcode-select -p > /dev/null 2>&1; then
  echo ""
  echo "First, macOS needs Apple's free command line tools. A window will open:"
  echo "click Install, wait for it to finish, then paste the install line again."
  xcode-select --install > /dev/null 2>&1 || true
  exit 0
fi

# 3. Ollama, which runs the AI model on this Mac.
OLLAMA="$(command -v ollama || true)"
[ -z "$OLLAMA" ] && [ -x /Applications/Ollama.app/Contents/Resources/ollama ] && OLLAMA=/Applications/Ollama.app/Contents/Resources/ollama
if [ -z "$OLLAMA" ]; then
  echo ""
  echo "MindChart uses Ollama to run its AI on this Mac. Opening Ollama's download page:"
  echo "install it, open it once, then paste the install line again."
  open "https://ollama.com/download/mac"
  exit 0
fi

# 4. MindChart itself, from its latest release.
echo "Downloading MindChart..."
RELEASE_URL=$(curl -fsSL "https://api.github.com/repos/$REPOSITORY/releases/latest" \
  | grep -o '"browser_download_url": *"[^"]*/MindChart.zip"' | sed 's/.*"\(https[^"]*\)"$/\1/')
if [ -z "$RELEASE_URL" ]; then
  echo "Couldn't find a MindChart release to download. Check the internet connection and try again."
  exit 1
fi
TEMP="$(mktemp -d)"
curl -fsSL "$RELEASE_URL" -o "$TEMP/MindChart.zip"
mkdir -p "$DEST"
unzip -oq "$TEMP/MindChart.zip" -d "$DEST"
rm -rf "$TEMP"
chmod +x "$DEST/Start MindChart.command" "$DEST/tools/pdftext" 2> /dev/null || true
echo "Installed version $(cat "$DEST/VERSION") in $DEST"

# 5. The AI model that suits this Mac's memory (MindChart's recommendations, largest first).
if [ "$MEMORY_GB" -ge 24 ]; then MODEL="gpt-oss:20b"
elif [ "$MEMORY_GB" -ge 16 ]; then MODEL="bonsai-2:27b"
else MODEL="bonsai-2:9b"; fi
if ! curl -s http://localhost:11434/api/version > /dev/null; then
  open -a Ollama
  for _ in $(seq 1 30); do curl -s http://localhost:11434/api/version > /dev/null && break; sleep 1; done
fi
echo ""
echo "Downloading the AI model ($MODEL). This is several GB and can take a while..."
"$OLLAMA" pull "$MODEL"

# 6. A shortcut on the Desktop.
SHORTCUT="$HOME/Desktop/MindChart.command"
printf '#!/bin/bash\nexec "%s/Start MindChart.command"\n' "$DEST" > "$SHORTCUT"
chmod +x "$SHORTCUT"

echo ""
echo "Done! Double-click \"MindChart\" on your Desktop to start."
echo "The first time, MindChart asks you to choose a password that protects your records."
echo "Please use only the fictional sample records in $DEST/sample-records while testing."
