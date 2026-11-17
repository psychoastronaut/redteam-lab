#!/usr/bin/env python3
"""
Painel web local pra disparar o agent.py/orchestrator.py sem terminal.

So aceita UM job por vez (trava de verdade, nao so convencao) -- e
exatamente o bug de dois processos simultaneos que ja pegamos hoje.
Cada job roda com timeout de seguranca e qualquer excecao cai no
estado do job em vez de travar a pagina ou o processo.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, Response

import guardrails
from playbooks import PLAYBOOKS

AGENT_DIR = Path("/agent")
RUNS_DIR = Path("/workspace/webui_runs")
RUNS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_TARGET = os.environ.get("ALLOWED_TARGET", "10.64.0.0/12")
MAX_RUNTIME_SECONDS = int(os.environ.get("WEBUI_MAX_RUNTIME", "1800"))  # 30 min

app = Flask(__name__)

_lock = threading.Lock()
_job = {
    "status": "idle",       # idle | running | done | failed | killed | timeout
    "mode": None,
    "target": None,
    "goal": None,
    "playbook": None,
    "pid": None,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "error": None,
    "log_path": None,
}
_proc_handle = {"proc": None}


def _watchdog(proc: subprocess.Popen, started_at: float) -> None:
    """Mata o processo se ele passar do tempo maximo, e sempre fecha o job no final."""
    try:
        while proc.poll() is None:
            if time.time() - started_at > MAX_RUNTIME_SECONDS:
                proc.terminate()
                time.sleep(3)
                if proc.poll() is None:
                    proc.kill()
                with _lock:
                    _job["status"] = "timeout"
                    _job["finished_at"] = time.time()
                    _job["exit_code"] = proc.poll()
                return
            time.sleep(2)

        with _lock:
            if _job["status"] == "running":  # nao sobrescreve timeout/killed
                _job["status"] = "done" if proc.returncode == 0 else "failed"
                _job["finished_at"] = time.time()
                _job["exit_code"] = proc.returncode
    except Exception as e:  # nunca deixa o watchdog morrer silencioso
        with _lock:
            _job["status"] = "failed"
            _job["error"] = f"watchdog crashed: {e}"
            _job["finished_at"] = time.time()


@app.route("/")
def index() -> str:
    return INDEX_HTML


@app.route("/api/status")
def status():
    with _lock:
        return jsonify(dict(_job))


@app.route("/api/log")
def log():
    offset = int(request.args.get("offset", "0"))
    with _lock:
        path = _job["log_path"]
    if not path or not Path(path).exists():
        return jsonify({"text": "", "offset": 0})
    with open(path, "r", errors="replace") as f:
        f.seek(offset)
        text = f.read()
        new_offset = f.tell()
    return jsonify({"text": text, "offset": new_offset})


@app.route("/api/run", methods=["POST"])
def run():
    data = request.get_json(force=True, silent=True) or {}
    target = str(data.get("target", "")).strip()
    goal = str(data.get("goal", "")).strip()
    mode = str(data.get("mode", "orchestrator")).strip()
    # "" (ou ausente) == "nenhum playbook" -- continua sendo o default.
    playbook = str(data.get("playbook", "")).strip()

    if not target or not goal:
        return jsonify({"error": "target e goal sao obrigatorios"}), 400
    if mode not in ("orchestrator", "agent"):
        return jsonify({"error": "mode precisa ser 'orchestrator' ou 'agent'"}), 400
    # valida contra a lista real de playbooks conhecidos -- nunca aceita
    # nome arbitrario vindo do POST (isso vai direto pro argv do subprocess).
    if playbook and playbook not in PLAYBOOKS:
        opcoes = ", ".join(sorted(PLAYBOOKS.keys()))
        return jsonify({"error": f"playbook '{playbook}' desconhecido. Opcoes: {opcoes}"}), 400
    # agent.py (modo monolitico) nao tem --playbook; em vez de ignorar
    # silenciosamente uma escolha do operador, erra de forma explicita.
    if playbook and mode == "agent":
        return jsonify({"error": "playbook so se aplica ao modo 'orchestrator' -- modo 'agent' nao tem essa flag"}), 400

    try:
        guardrails.check_scope(target, ALLOWED_TARGET)
    except guardrails.GuardrailViolation as e:
        return jsonify({"error": f"alvo fora do escopo: {e}"}), 400

    with _lock:
        if _job["status"] == "running":
            return jsonify({"error": "ja tem um job rodando -- espere terminar ou pare ele"}), 409

        # trava de verdade: confia no SO, nao so na memoria do processo Flask.
        # ja vimos hoje a memoria dizer "parado" com o processo ainda vivo.
        leftover = subprocess.run(
            ["pgrep", "-f", r"(orchestrator|agent)\.py"],
            capture_output=True, text=True,
        )
        if leftover.returncode == 0 and leftover.stdout.strip():
            pids = leftover.stdout.strip().replace("\n", ", ")
            return jsonify({
                "error": f"processo(s) orfao(s) detectado(s) (pid {pids}) -- "
                         f"pare manualmente antes de iniciar um novo job",
            }), 409

        script = AGENT_DIR / ("orchestrator.py" if mode == "orchestrator" else "agent.py")
        ts = time.strftime("%Y%m%d-%H%M%S")
        log_path = RUNS_DIR / f"{ts}_{mode}.log"

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"  # senao o log so aparece quando o processo termina
        # WORKER_MODEL_OVERRIDE permite corrigir o modelo sem recriar o container
        # (a env var normal WORKER_MODEL fica fixada na criacao do container)
        env["WORKER_MODEL"] = os.environ.get("WORKER_MODEL_OVERRIDE", env.get("WORKER_MODEL", "qwen2.5:7b-instruct"))

        cmd = ["python3", "-u", str(script), "--target", target, "--goal", goal]
        if playbook and mode == "orchestrator":
            cmd += ["--playbook", playbook]

        try:
            log_file = open(log_path, "w", buffering=1)
            proc = subprocess.Popen(
                cmd,
                stdout=log_file, stderr=subprocess.STDOUT,
                cwd="/workspace", text=True, env=env,
            )
        except Exception as e:
            return jsonify({"error": f"falha ao iniciar processo: {e}"}), 500

        started_at = time.time()
        _proc_handle["proc"] = proc
        _job.update({
            "status": "running", "mode": mode, "target": target, "goal": goal,
            "playbook": playbook or None,
            "pid": proc.pid, "started_at": started_at, "finished_at": None,
            "exit_code": None, "error": None, "log_path": str(log_path),
        })

    threading.Thread(target=_watchdog, args=(proc, started_at), daemon=True).start()
    return jsonify({"started": True, "log_path": str(log_path)})


@app.route("/api/stop", methods=["POST"])
def stop():
    with _lock:
        proc = _proc_handle.get("proc")
        if not proc or proc.poll() is not None:
            return jsonify({"error": "nenhum job rodando"}), 400

    proc.terminate()
    try:
        proc.wait(timeout=5)  # espera a morte de verdade, nao so assume
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)  # SIGKILL quase nunca falha, mas confirma mesmo assim
        except subprocess.TimeoutExpired:
            with _lock:
                _job["error"] = f"processo pid {proc.pid} nao morreu nem com SIGKILL -- mate manualmente"
            return jsonify({"error": f"falha ao matar pid {proc.pid}, intervencao manual necessaria"}), 500

    with _lock:
        _job["status"] = "killed"
        _job["finished_at"] = time.time()
        _job["exit_code"] = proc.returncode
    return jsonify({"stopped": True, "exit_code": proc.returncode})


INDEX_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Redteam Lab</title>
<style>
  body { font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 2rem; }
  h1 { color: #58a6ff; }
  input, select, textarea { background: #161b22; color: #c9d1d9; border: 1px solid #30363d; padding: 6px; }
  input[type=text] { width: 300px; }
  button { background: #238636; color: white; border: none; padding: 8px 16px; cursor: pointer; }
  button.stop { background: #da3633; }
  button:disabled { background: #30363d; cursor: not-allowed; }
  #log { background: #010409; border: 1px solid #30363d; padding: 1rem; height: 420px;
         overflow-y: scroll; white-space: pre-wrap; margin-top: 1rem; font-size: 0.85rem; }
  .badge { padding: 2px 8px; border-radius: 4px; font-weight: bold; }
  .idle { background: #30363d; } .running { background: #9e6a03; }
  .done { background: #238636; } .failed, .timeout, .killed { background: #da3633; }
  label { display: inline-block; width: 90px; }
  .row { margin-bottom: 8px; }
</style>
</head>
<body>
  <h1>Redteam Lab</h1>
  <div class="row"><label>Target</label><input id="target" type="text" placeholder="10.67.177.103"></div>
  <div class="row"><label>Goal</label><input id="goal" type="text" style="width:500px" placeholder="pegar user.txt"></div>
  <div class="row"><label>Modo</label>
    <select id="mode">
      <option value="orchestrator">orchestrator (planner + worker)</option>
      <option value="agent">agent (monolitico)</option>
    </select>
  </div>
  <div class="row"><label>Playbook</label>
    <select id="playbook">
      <option value="">nenhum</option>
      __PLAYBOOK_OPTIONS__
    </select>
    <span style="font-size:0.8rem;color:#8b949e">so vale pro modo orchestrator -- erro no modo agent</span>
  </div>
  <div class="row">
    <button id="start">iniciar</button>
    <button id="stop" class="stop">parar</button>
    status: <span id="badge" class="badge idle">idle</span>
  </div>
  <div id="log"></div>

<script>
let logOffset = 0;

async function poll() {
  try {
    const s = await fetch('/api/status').then(r => r.json());
    const badge = document.getElementById('badge');
    badge.textContent = s.status;
    badge.className = 'badge ' + s.status;
    document.getElementById('start').disabled = (s.status === 'running');

    const l = await fetch('/api/log?offset=' + logOffset).then(r => r.json());
    if (l.text) {
      const box = document.getElementById('log');
      box.textContent += l.text;
      box.scrollTop = box.scrollHeight;
      logOffset = l.offset;
    }
  } catch (e) {
    console.error(e);
  }
  setTimeout(poll, 1500);
}

document.getElementById('start').onclick = async () => {
  logOffset = 0;
  document.getElementById('log').textContent = '';
  const body = {
    target: document.getElementById('target').value,
    goal: document.getElementById('goal').value,
    mode: document.getElementById('mode').value,
    playbook: document.getElementById('playbook').value,
  };
  const r = await fetch('/api/run', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  const j = await r.json();
  if (j.error) alert(j.error);
};

document.getElementById('stop').onclick = async () => {
  const r = await fetch('/api/stop', { method: 'POST' });
  const j = await r.json();
  if (j.error) alert(j.error);
};

poll();
</script>
</body>
</html>
"""

# monta as <option> do select de playbook a partir do registro real em
# playbooks/__init__.py -- nunca hardcoded aqui, pra nao desalinhar se
# um playbook for adicionado/removido.
_PLAYBOOK_OPTIONS_HTML = "\n      ".join(
    f'<option value="{nome}">{nome}</option>' for nome in sorted(PLAYBOOKS.keys())
)
INDEX_HTML = INDEX_HTML.replace("__PLAYBOOK_OPTIONS__", _PLAYBOOK_OPTIONS_HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
