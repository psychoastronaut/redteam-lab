#!/usr/bin/env python3
"""
Loop de agente de redteam contra maquinas do TryHackMe, falando com um
modelo local via Ollama (tool-calling nativo).

Uso:
    python3 agent.py --target 10.10.x.x --goal "pegar user.txt"

Cada chamada de ferramenta passa pelo guardrail de escopo (guardrails.py)
antes de executar, e por padrao pede confirmacao no terminal (EXEC_MODE=confirm).
"""

import argparse
import json
import os
import sys

import requests

from llm_client import chat_with_retry
from tools import TOOL_SCHEMAS, DISPATCH

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b-instruct")
ALLOWED_TARGET = os.environ.get("ALLOWED_TARGET", "10.64.0.0/12")

SYSTEM_PROMPT = """You are a careful offensive security assistant working \
inside an authorized TryHackMe lab, through a Kali container with real \
pentest tools available via the run_command tool.

Rules:
- Stay strictly inside the authorized target scope given by the operator.
- Work in small, verifiable steps: reconnaissance before exploitation,
  one tool call at a time, read the output before deciding the next step.
- After every meaningful finding (open port, service version, credential,
  hash, flag, vulnerable endpoint) call write_note to persist it.
- Call read_notes at the start of a session or after being interrupted, so
  you don't repeat scans you already ran.
- Explain your reasoning briefly before each tool call, in plain text.
- If a command is blocked or rejected, do not retry the same command;
  adapt your approach.
- Never suggest commands against hosts outside the authorized scope.

Use the dedicated tools (run_nmap, run_gobuster, run_hydra, run_nikto,
run_sqlmap) instead of run_command whenever they cover what you need --
they build correct syntax and validate wordlists for you, so you can't
mistype a flag or point at a wordlist that doesn't exist. Only fall
back to run_command for tools without a dedicated wrapper (whatweb,
dirb, curl, dig, john, crackmapexec, etc), or for anything those
wrappers don't expose. If you need a username list for brute force,
create one first with a shell redirect into /workspace via run_command,
never assume one exists.
"""


def chat(messages: list[dict]) -> tuple[dict, list[dict]]:
    """Chama o Ollama com retentativa + compressao de historico em caso
    de falha (ver llm_client.py). Retorna (resposta, historico efetivo) --
    o chamador deve seguir usando o historico retornado."""
    return chat_with_retry(OLLAMA_HOST, MODEL, messages, TOOL_SCHEMAS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="IP da maquina THM (dentro do escopo VPN)")
    parser.add_argument("--goal", required=True, help="objetivo da sessao, ex: 'pegar user.txt'")
    parser.add_argument("--max-steps", type=int, default=40)
    args = parser.parse_args()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Target autorizado: {args.target} (escopo permitido: {ALLOWED_TARGET}). "
                f"Objetivo desta sessao: {args.goal}. Comece com reconhecimento."
            ),
        },
    ]

    for step in range(1, args.max_steps + 1):
        print(f"\n===== passo {step}/{args.max_steps} =====")
        try:
            response, messages = chat(messages)
        except requests.RequestException as e:
            print(f"[erro ao falar com o Ollama, mesmo apos retentativa com historico comprimido] {e}")
            sys.exit(1)

        msg = response.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or []

        if content:
            print(f"\n[modelo] {content}")
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            print("\n[o modelo nao chamou nenhuma ferramenta -- sessao parece concluida ou travada]")
            break

        for call in tool_calls:
            fn = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            fn_args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

            handler = DISPATCH.get(fn)
            if handler is None:
                result = f"[erro] ferramenta desconhecida: {fn}"
            else:
                result = handler(fn_args)

            print(f"\n[ferramenta: {fn}] -> {str(result)[:800]}")
            messages.append({"role": "tool", "content": str(result)})
    else:
        print("\n[limite de passos atingido]")


if __name__ == "__main__":
    main()
