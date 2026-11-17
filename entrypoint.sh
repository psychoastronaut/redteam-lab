#!/bin/bash
# Mantem o painel web vivo: se webui.py cair por qualquer motivo, religa
# depois de 2s em vez de deixar o container sem interface.
(
  while true; do
    python3 /agent/webui.py >> /workspace/webui.log 2>&1
    echo "[entrypoint] webui.py caiu, religando em 2s..." >> /workspace/webui.log
    sleep 2
  done
) &

# mantem o container vivo mesmo que o loop acima morra de algum jeito bizarro
tail -f /dev/null
