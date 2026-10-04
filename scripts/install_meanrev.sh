#!/bin/sh
# Copy MeanRev_EA + EACore.mqh into MT5's Advisors folder and compile with MetaEditor. Exit 1 on errors.
set -e
cd "$(dirname "$0")/.."
ADV="$HOME/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Advisors"
cp mql5/EACore.mqh mql5/MeanRev_EA.mq5 "$ADV/"
"$HOME/.local/bin/mbt-wine-py" -c "
import sys; sys.path.insert(0, '.')
from core.compiler import compile_mql5
r = compile_mql5('C:/Program Files/MetaTrader 5/MQL5/Experts/Advisors/MeanRev_EA.mq5')
print({k: r.get(k) for k in ('ok', 'errors', 'warnings', 'messages', 'error')})
sys.exit(0 if r.get('ok') and not r.get('warnings') else 1)
" 2>/dev/null
