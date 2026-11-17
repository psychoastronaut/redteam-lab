"""
Worker: executa UMA subtask, num loop curto e com modelo leve.
Devolve um resumo compacto pro planner, nunca a saida bruta das
ferramentas (isso fica em workspace/log.jsonl e notes.md, como sempre).
"""

import json
import os

import requests

from llm_client import chat_with_retry
from tools import TOOL_SCHEMAS, DISPATCH

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
WORKER_MODEL = os.environ.get("WORKER_MODEL", "qwen2.5:7b-instruct")
WORKER_MAX_STEPS = int(os.environ.get("WORKER_MAX_STEPS", "10"))

WORKER_SYSTEM_PROMPT = """You are a focused execution agent inside an \
authorized TryHackMe Kali container. You receive ONE subtask from a \
planner and a success criterion. Work only on that subtask.

Rules:
- Use run_command for actual pentest actions. One command per turn,
  read its output before deciding the next command.
- Call write_note for anything worth remembering (open port, service
  version, credential, hash, flag, interesting path).
- ALWAYS call read_notes as your very first action, before anything
  else -- previous subtasks in this session may have already found
  what you're looking for. Don't redo recon that's already in the notes.
- If you catch yourself describing a command in text/markdown instead
  of calling a tool, STOP and call the tool instead. Never write a
  ```bash``` block as your answer -- that is not an action, it does
  nothing. Every turn must either call a tool or call finish_subtask.
- When the subtask's success criterion is met, or you are stuck after
  a few failed attempts, call finish_subtask with a short summary
  (2-4 sentences) and a status of "done", "partial" or "blocked".
  Do not call any other tool after finish_subtask.
- Never exceed the subtask's scope. Never repeat an identical command
  that already failed; adapt instead.

Available wordlists (always use the FULL absolute path, these files do
not exist under /workspace):
- /usr/share/wordlists/rockyou.txt (passwords, decompressed)
- /usr/share/wordlists/dirb/common.txt, small.txt, big.txt (web dirs)
- /usr/share/seclists/Discovery/Web-Content/ (bigger web dir/file lists)
- /usr/share/seclists/Passwords/ (other password lists)
Use the dedicated tools (run_nmap, run_gobuster, run_hydra, run_nikto,
run_sqlmap) instead of run_command whenever they cover what you need --
they build correct syntax and validate wordlists for you, so you can't
mistype a flag or point at a wordlist that doesn't exist. Only fall
back to run_command for tools without a dedicated wrapper (whatweb,
dirb, curl, dig, john, crackmapexec, etc), or for anything those
wrappers don't expose.
"""

WORKER_TOOLS = TOOL_SCHEMAS + [
    {
        "type": "function",
        "function": {
            "name": "finish_subtask",
            "description": "Ends this subtask and reports back to the planner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "partial", "blocked"]},
                    "summary": {"type": "string", "description": "2-4 sentence summary of what was found/done"},
                },
                "required": ["status", "summary"],
            },
        },
    },
]


def _chat(messages: list[dict]) -> tuple[dict, list[dict]]:
    """Chama o Ollama com retentativa + compressao de historico em caso
    de falha (ver llm_client.py). Retorna (resposta, historico efetivo) --
    o chamador deve seguir usando o historico retornado."""
    return chat_with_retry(OLLAMA_HOST, WORKER_MODEL, messages, WORKER_TOOLS)


def run_subtask(target: str, subtask: str, success_criteria: str) -> dict:
    """Roda o loop do worker. Retorna {"status": ..., "summary": ...}."""
    messages = [
        {"role": "system", "content": WORKER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Target: {target}\n"
                f"Subtask: {subtask}\n"
                f"Success criteria: {success_criteria}\n"
                f"Begin."
            ),
        },
    ]

    narrating_strikes = 0
    MAX_NARRATING_STRIKES = 2  # quantas vezes deixamos o modelo so "descrever" antes de desistir

    for step in range(1, WORKER_MAX_STEPS + 1):
        try:
            response, messages = _chat(messages)
        except requests.RequestException as e:
            return {
                "status": "blocked",
                "summary": f"falha ao falar com o Ollama, mesmo apos retentativa com historico comprimido: {e}",
            }
        msg = response.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            narrating_strikes += 1
            if narrating_strikes > MAX_NARRATING_STRIKES:
                return {
                    "status": "partial",
                    "summary": content or f"worker ficou so narrando, sem chamar ferramenta, apos {MAX_NARRATING_STRIKES} avisos.",
                }
            # nao desiste de cara: empurra o modelo a agir em vez de so descrever
            messages.append({
                "role": "user",
                "content": (
                    "Voce descreveu uma acao mas NAO chamou nenhuma ferramenta. "
                    "Chame AGORA a ferramenta certa (run_nmap/run_gobuster/run_hydra/"
                    "run_nikto/run_sqlmap/run_command/write_note/read_notes) com os "
                    "argumentos reais, ou chame finish_subtask se nao for possivel continuar. "
                    "Nao descreva o comando em texto -- EXECUTE-O via tool call."
                ),
            })
            continue

        for call in tool_calls:
            fn = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            fn_args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

            if fn == "finish_subtask":
                return {
                    "status": fn_args.get("status", "partial"),
                    "summary": fn_args.get("summary", ""),
                }

            handler = DISPATCH.get(fn)
            result = handler(fn_args) if handler else f"[erro] ferramenta desconhecida: {fn}"
            messages.append({"role": "tool", "content": str(result)})

    return {"status": "partial", "summary": f"worker atingiu o limite de {WORKER_MAX_STEPS} passos sem concluir."}
