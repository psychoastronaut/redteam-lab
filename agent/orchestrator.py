#!/usr/bin/env python3
"""
Orquestrador: modelo grande planeja, modelo leve executa.

Uso:
    python3 orchestrator.py --target 10.10.x.x --goal "pegar user.txt"
    python3 orchestrator.py --target 10.10.x.x --goal "pegar user.txt" --playbook web

O planner NAO tem acesso a run_command. Ele so pode despachar subtasks
pro worker (worker.py) ou concluir a sessao. O worker e quem de fato
roda comando, com o mesmo guardrail de escopo de sempre.

--playbook e opcional. Quando passado, o texto do playbook (fases e
tecnicas SUGERIDAS, ver playbooks/base.py) entra junto na primeira
mensagem do planner -- o planner continua livre pra pular fase, mudar
ordem ou ignorar tecnica que nao se aplica aquela sala. Sem --playbook,
a mensagem inicial e byte-a-byte igual a de antes (so target/goal,
"Target: ...\nGoal: ...\nBegin planning.").
"""

import argparse
import json
import os

import requests

from llm_client import chat_with_retry
from worker import run_subtask
from tools import read_notes
from playbooks import PLAYBOOKS

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "qwen2.5:7b-instruct")
PLANNER_MAX_PHASES = int(os.environ.get("PLANNER_MAX_PHASES", "8"))

PLANNER_SYSTEM_PROMPT = """You are the planning layer of an authorized \
TryHackMe red-team agent. You do NOT execute commands yourself -- you \
only break the goal into subtasks and dispatch them, one at a time, to \
a worker that has real pentest tools.

Rules:
- Think in phases: recon -> service enumeration -> vulnerability /
  credential discovery -> exploitation -> post-exploitation / loot.
- Dispatch ONE subtask at a time via dispatch_subtask, with a clear,
  narrow success_criteria so the worker knows when to stop.
- After each subtask's summary comes back, decide: dispatch the next
  subtask, or call conclude if the goal is reached or clearly
  unreachable with remaining budget.
- Keep subtasks small and concrete (e.g. "enumerate open ports and
  service versions", "brute-force directories on the web server",
  "try default credentials on the login panel found"), never vague
  ("hack the box").
- Call read_notes if you need to recall what earlier subtasks found.
"""

PLANNER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "dispatch_subtask",
            "description": "Sends one focused subtask to the execution worker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subtask": {"type": "string"},
                    "success_criteria": {"type": "string"},
                },
                "required": ["subtask", "success_criteria"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notes",
            "description": "Reads accumulated findings from previous subtasks.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conclude",
            "description": "Ends the session: goal reached, or not reachable with remaining budget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["goal_reached", "not_reached"]},
                    "summary": {"type": "string"},
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
    return chat_with_retry(OLLAMA_HOST, PLANNER_MODEL, messages, PLANNER_TOOLS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument(
        "--playbook", choices=sorted(PLAYBOOKS.keys()), default=None,
        help="playbook opcional com fases/tecnicas sugeridas (ver agent/playbooks/). "
             "sem essa flag, o planner so recebe target e goal, como sempre.",
    )
    args = parser.parse_args()

    user_content = f"Target: {args.target}\nGoal: {args.goal}"
    if args.playbook:
        playbook = PLAYBOOKS[args.playbook]()
        playbook_text = playbook.get_task().rstrip("\n")
        user_content += (
            f"\n\nPlaybook sugerido ('{playbook.name}') -- use como ponto de "
            "partida, NAO como roteiro rigido. Voce decide a ordem real, "
            "pode pular fase/tecnica que nao se aplica a esta maquina, e "
            "pode fazer coisas fora do playbook se o reconhecimento indicar "
            "outro caminho melhor:\n\n"
            f"{playbook_text}"
        )
    user_content += "\nBegin planning."

    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": user_content,
        },
    ]

    for phase in range(1, PLANNER_MAX_PHASES + 1):
        print(f"\n########## FASE {phase}/{PLANNER_MAX_PHASES} (planner) ##########")
        try:
            response, messages = _chat(messages)
        except requests.RequestException as e:
            print(f"\n[erro ao falar com o Ollama, mesmo apos retentativa com historico comprimido] {e}")
            print("[encerrando sessao do planner]")
            return
        msg = response.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or []
        if content:
            print(f"[planner] {content}")
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            print("[planner nao chamou ferramenta -- encerrando]")
            return

        stop = False
        for call in tool_calls:
            fn = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            fn_args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

            if fn == "conclude":
                print(f"\n===== CONCLUSAO: {fn_args.get('status')} =====")
                print(fn_args.get("summary", ""))
                stop = True
                messages.append({"role": "tool", "content": "sessao encerrada pelo planner."})
                continue

            if fn == "read_notes":
                result = read_notes()
                messages.append({"role": "tool", "content": result})
                continue

            if fn == "dispatch_subtask":
                subtask = fn_args.get("subtask", "")
                success_criteria = fn_args.get("success_criteria", "")
                print(f"\n>>> despachando pro worker: {subtask!r} (critério: {success_criteria!r})")
                result = run_subtask(args.target, subtask, success_criteria)
                print(f"<<< worker voltou [{result['status']}]: {result['summary']}")
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False),
                })
                continue

            messages.append({"role": "tool", "content": f"[erro] ferramenta desconhecida: {fn}"})

        if stop:
            return

    print("\n[limite de fases do planner atingido]")


if __name__ == "__main__":
    main()
