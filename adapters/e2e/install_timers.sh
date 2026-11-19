#!/bin/bash
# Put e2e's intake timers under reins on this machine: the drop-in run-control.conf beside each installed unit.
# e2e installs its own units (tools/install_intake_timers.sh); without this drop-in they run on their own.
set -euo pipefail
D=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
for u in intake-watch.service intake-register.service; do
  [ -f "$D/$u" ] || { echo "$u not installed (e2e: tools/install_intake_timers.sh)"; exit 1; }
  mkdir -p "$D/$u.d"
  cp "$(dirname "$(readlink -f "$0")")/systemd/run-control.conf" "$D/$u.d/run-control.conf"
done
systemctl --user daemon-reload
echo "run control on: $(systemctl --user show intake-watch.service -p Environment --value)"
