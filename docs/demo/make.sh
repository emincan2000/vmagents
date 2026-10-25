#!/usr/bin/env bash
# Makes docs/demo.gif: the command line, the terminal UI, the phone page and
# a VM's desktop in the browser.
# Needs: a VM "arch" with ~/demo in it, Claude Code logged in (folder
# trusted) and the other four agents installed (vmforai agent arch codex, ...).
# Codex, OpenCode and aider answer through OpenRouter's free models: its key
# in their AGENT-env files on arch, the model in their own settings there;
# Gemini CLI only starts. Here: asciinema 2.4 (uv tool run --from
# asciinema==2.4.0 asciinema), agg (github.com/asciinema/agg), chromium, node,
# uv (for Pillow), ImageMagick 7. A VM "dev" with the desktop (vmforai
# create dev --template desktop) for the last part. The phone's task changes ~/demo on
# arch: take that back before the next run.
# The phone page runs from its own server with a made-up ntfy topic
# (phone-self.sh) and a throwaway key: no real key or topic in the pictures.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-$here/out}
mkdir -p "$out/phone/dir" "$out/phone/cache"
cd "$out"
rec() { TERM=xterm-256color uv tool run --from 'asciinema==2.4.0' asciinema rec --overwrite -q "$@"; }
rec --cols 92 --rows 26 -c "bash $here/cli.sh" cli.cast
rec --cols 120 --rows 32 -c "python3 $here/ui.py" ui.cast
# Claude Code's own usage notice ("You've used N% of your weekly limit ...") is
# blanked out, the same width so nothing moves.
python3 -c 'import re, sys; p = sys.argv[1]; s = open(p, encoding="utf-8").read()
s = re.sub(r"You\x27ve used \d+% of your \w* ?limit · resets [^\\\\\"]*?\(UTC\)", lambda m: " " * len(m[0]), s)
open(p, "w", encoding="utf-8").write(s)' ui.cast
font=(--font-family "JetBrains Mono,Fira Code,DejaVu Sans Mono" --theme monokai)
agg --speed 1.5 --idle-time-limit 1.0 --last-frame-duration 2.5 --font-size 15 "${font[@]}" cli.cast cli.gif
agg --speed 1.6 --idle-time-limit 1.0 --last-frame-duration 2 --font-size 14 "${font[@]}" ui.cast ui.gif

# The phone page: its server with the real VMs, recorded at phone size.
echo demo-key >phone/dir/token
# shellcheck disable=SC2016 # Python, not the shell
python3 -c 'import sys; s=open(sys.argv[1]).read(); m="PHONE_PY=$(cat <<\x27PHONE_END\x27\n"; print(s[s.index(m)+len(m):s.index("\nPHONE_END\n")])' \
  "$(command -v vmforai)" >phone/phone.py
cp -r "${XDG_CACHE_HOME:-$HOME/.cache}/vmforai/phone-lib" phone/cache/ 2>/dev/null || true
VMFORAI_CACHE=$out/phone/cache VMFORAI_PHONE_FETCH=0 python3 phone/phone.py "$here/phone-self.sh" "$out/phone/dir" 127.0.0.1 47201 &
server=$!
trap 'kill $server 2>/dev/null' EXIT
until curl -s -o /dev/null http://127.0.0.1:47201/; do sleep 0.3; done
node "$here/phone.mjs" "$out/phone" "http://127.0.0.1:47201/#t=demo-key"
uv run -q --no-project --with pillow python "$here/video.py" "$out/phone" phone.gif phone

# The desktop: vmforai view in the browser, on a VM "dev" made with
# --template desktop. A made-up xdg-open catches the page's address.
mkdir -p desktop/bin
# shellcheck disable=SC2016 # $1 is the fake xdg-open's
printf '#!/bin/sh\necho "$1" >%s/desktop/url\n' "$out" >desktop/bin/xdg-open
chmod +x desktop/bin/xdg-open
rm -f desktop/url
PATH=$out/desktop/bin:$PATH vmforai view dev --browser --keep >desktop/view.log 2>&1 &
view=$!
trap 'kill $server 2>/dev/null; kill -INT $view 2>/dev/null' EXIT
until [ -s desktop/url ]; do sleep 0.3; done
node "$here/desktop.mjs" "$out/desktop" "$(cat desktop/url)"
uv run -q --no-project --with pillow python "$here/video.py" "$out/desktop" desktop.gif window \
  "127.0.0.1:6080 · dev (vmforai)"

BG='#272822' W=1024 H=646
card() { magick -size ${W}x${H} xc:"$BG" -gravity center -font DejaVu-Sans-Bold -pointsize 48 -fill '#f8f8f2' \
  -annotate +0-24 "$2" -font DejaVu-Sans -pointsize 22 -fill '#a6e22e' -annotate +0+38 "$3" "$1"; }
card intro.png "vmforai" "AI coding agents in a virtual machine, not on your computer"
card c1.png "1 · The command line" "give an agent a task, then take back all it did"
card c2.png "2 · Five agents, one terminal UI" "Claude Code, Codex, Gemini CLI, OpenCode, aider: each in a tab"
card c3.png "3 · On your phone" "open a VM, give it a task, watch the agent do it"
card c4.png "4 · A desktop for the agents" "vmforai view: the VM's screen, here in your browser"
card outro.png "vmforai" "github.com/emincan2000/vmforai"

# All in one GIF with one palette (ffmpeg: ImageMagick runs out of memory on it).
fit="scale=$W:$H:force_original_aspect_ratio=decrease,pad=$W:$H:(ow-iw)/2:(oh-ih)/2:color=$BG,setsar=1,fps=10"
ffmpeg -loglevel error -y \
  -loop 1 -t 2 -i intro.png -loop 1 -t 1.3 -i c1.png -i cli.gif -loop 1 -t 1.5 -i c2.png -i ui.gif \
  -loop 1 -t 1.3 -i c3.png -i phone.gif -loop 1 -t 1.5 -i c4.png -i desktop.gif -loop 1 -t 3 -i outro.png \
  -filter_complex "$(for i in 0 1 2 3 4 5 6 7 8 9; do printf '[%d]%s[v%d];' $i "$fit" $i; done)\
[v0][v1][v2][v3][v4][v5][v6][v7][v8][v9]concat=n=10:v=1,split[a][b];[a]palettegen=max_colors=192[p];\
[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" -loop 0 "$here/../demo.gif"
echo "docs/demo.gif"
