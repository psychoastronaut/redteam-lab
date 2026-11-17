"""
Ferramentas expostas ao modelo. Cada uma loga o que fez em
/workspace/log.jsonl antes de qualquer outra coisa, pra sempre ter
rastro do que o agente tentou -- mesmo quando o guardrail bloqueia.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from guardrails import GuardrailViolation, validate

WORKSPACE = Path("/workspace")
LOG_PATH = WORKSPACE / "log.jsonl"
NOTES_PATH = WORKSPACE / "notes.md"
ALLOWED_TARGET = os.environ.get("ALLOWED_TARGET", "10.64.0.0/12")
EXEC_MODE = os.environ.get("EXEC_MODE", "confirm")  # "confirm" ou "auto"


def _log(entry: dict) -> None:
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_command(command: str, purpose: str = "") -> str:
    """Executa um comando de shell dentro do container Kali, com freio de escopo."""
    try:
        validate(command, ALLOWED_TARGET)
    except GuardrailViolation as e:
        _log({"tool": "run_command", "command": command, "purpose": purpose,
              "status": "blocked", "reason": str(e)})
        return f"[BLOQUEADO PELO GUARDRAIL] {e}"

    if EXEC_MODE == "confirm":
        print(f"\n--- comando proposto ---\nmotivo: {purpose}\n$ {command}\n")
        resp = input("executar? [y/N] ").strip().lower()
        if resp != "y":
            _log({"tool": "run_command", "command": command, "purpose": purpose,
                  "status": "rejected_by_operator"})
            return "[REJEITADO PELO OPERADOR] comando nao executado."

    print(f"\n>>> executando (pode levar ate alguns minutos): $ {command}\n", flush=True)
    t0 = time.time()
    try:
        result = subprocess.run(
            command, shell=True, cwd=str(WORKSPACE),
            capture_output=True, text=True, timeout=300,
        )
        print(f"<<< terminou em {time.time() - t0:.1f}s (returncode {result.returncode})\n", flush=True)
        output = (result.stdout or "") + (result.stderr or "")
        output = output[-8000:]  # nao afogar o contexto do modelo
        _log({"tool": "run_command", "command": command, "purpose": purpose,
              "status": "executed", "returncode": result.returncode,
              "output_tail": output[-500:]})
        return output or "[sem saida]"
    except subprocess.TimeoutExpired:
        print(f"<<< TIMEOUT depois de {time.time() - t0:.1f}s\n", flush=True)
        _log({"tool": "run_command", "command": command, "purpose": purpose,
              "status": "timeout"})
        return "[TIMEOUT] comando passou de 300s e foi interrompido."


WORDLIST_ALIASES = {
    "": "/usr/share/wordlists/dirb/common.txt",
    "small": "/usr/share/wordlists/dirb/small.txt",
    "common": "/usr/share/wordlists/dirb/common.txt",
    "big": "/usr/share/wordlists/dirb/big.txt",
    "seclists_small": "/usr/share/seclists/Discovery/Web-Content/DirBuster-2007_directory-list-2.3-small.txt",
    "seclists_medium": "/usr/share/seclists/Discovery/Web-Content/DirBuster-2007_directory-list-2.3-medium.txt",
    "seclists_big": "/usr/share/seclists/Discovery/Web-Content/DirBuster-2007_directory-list-2.3-big.txt",
    "rockyou": "/usr/share/wordlists/rockyou.txt",
}


def _resolve_wordlist(name_or_path: str) -> str:
    """Aceita um alias conhecido ou um caminho absoluto, confere que existe
    ANTES de montar qualquer comando -- isso elimina o erro que vimos hoje
    (gobuster/hydra apontando pra wordlist que nao existe)."""
    candidate = WORDLIST_ALIASES.get(name_or_path or "", name_or_path or WORDLIST_ALIASES[""])
    if not Path(candidate).exists():
        opcoes = ", ".join(sorted(k for k in WORDLIST_ALIASES if k))
        raise GuardrailViolation(
            f"wordlist '{candidate}' nao existe no container. "
            f"Use um alias conhecido ({opcoes}) ou um caminho absoluto real."
        )
    return candidate


def run_nmap(target: str, ports: str = "-", version_detection: bool = True,
             scripts: bool = False, purpose: str = "") -> str:
    """nmap com sintaxe sempre correta: so escolhe portas e se quer -sV/-sC."""
    flags = ["-Pn", "-p-" if ports == "-" else f"-p {ports}"]
    if version_detection:
        flags.append("-sV")
    if scripts:
        flags.append("-sC")
    cmd = f"nmap {' '.join(flags)} {target}"
    return run_command(cmd, purpose or "varredura nmap")


def run_gobuster(url: str, wordlist: str = "", extensions: str = "",
                  status_codes: str = "200,204,301,302,307,401,403",
                  threads: int = 50, purpose: str = "") -> str:
    """gobuster dir com flag certa sempre (--status-codes/-s, nao --status-code
    como o modelo ja chutou errado antes) e wordlist validada antes de rodar."""
    try:
        wl = _resolve_wordlist(wordlist)
    except GuardrailViolation as e:
        return f"[ERRO] {e}"
    # gobuster tem um blacklist de status "404" ligado por padrao, e recusa
    # rodar se -s (status-codes) e o blacklist default estiverem ativos juntos.
    # -b "" desliga o blacklist explicitamente -- sem isso, TODA chamada com
    # status_codes falha sempre, 100% das vezes.
    flags = [f"-u {url}", f"-w {wl}", f"-t {threads}", f"-s {status_codes}", '-b ""']
    if extensions:
        flags.append(f"-x {extensions}")
    cmd = "gobuster dir " + " ".join(flags)
    return run_command(cmd, purpose or "enumeracao de diretorios web")


def run_hydra(target: str, service: str, username: str = "", userlist: str = "",
               password: str = "", passlist: str = "", purpose: str = "") -> str:
    """hydra com -l/-L e -p/-P mutuamente exclusivos garantidos em codigo
    (o erro 'you can only use -L OR -l, not both' nao acontece mais)."""
    if bool(username) == bool(userlist):
        return "[ERRO] forneca EXATAMENTE um entre 'username' (-l) ou 'userlist' (-L), nunca os dois nem nenhum."
    if bool(password) == bool(passlist):
        return "[ERRO] forneca EXATAMENTE um entre 'password' (-p) ou 'passlist' (-P), nunca os dois nem nenhum."

    if userlist and not Path(userlist).exists():
        return (
            f"[ERRO] userlist '{userlist}' nao existe. Crie o arquivo primeiro com "
            f"run_command (ex: echo -e 'root\\nuser' > /workspace/users.txt) e use o caminho completo."
        )

    user_flag = f"-l {username}" if username else f"-L {userlist}"
    if password:
        pass_flag = f"-p {password}"
    else:
        try:
            pl = _resolve_wordlist(passlist)
        except GuardrailViolation as e:
            return f"[ERRO] {e}"
        pass_flag = f"-P {pl}"

    cmd = f"hydra {user_flag} {pass_flag} {target} {service}"
    return run_command(cmd, purpose or f"forca bruta {service}")


def run_nikto(url: str, purpose: str = "") -> str:
    """nikto com -maxtime pra garantir que termina dentro do timeout do run_command."""
    return run_command(f"nikto -h {url} -maxtime 280s", purpose or "varredura web nikto")


def run_sqlmap(url: str, data: str = "", extra_flags: str = "", purpose: str = "") -> str:
    flags = [f"-u {url}", "--batch"]
    if data:
        flags.append(f"--data='{data}'")
    if extra_flags:
        flags.append(extra_flags)
    cmd = "sqlmap " + " ".join(flags)
    return run_command(cmd, purpose or "teste sqlmap")


def write_note(title: str, content: str) -> str:
    with NOTES_PATH.open("a") as f:
        f.write(f"\n## {title}\n\n{content}\n")
    _log({"tool": "write_note", "title": title})
    return "nota salva em /workspace/notes.md"


def read_notes() -> str:
    if NOTES_PATH.exists():
        return NOTES_PATH.read_text()[-6000:]
    return "[ainda sem notas]"


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_nmap",
            "description": "Varredura de portas/servicos com nmap. Prefira esta ferramenta a run_command para nmap.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "ports": {"type": "string", "description": "'-' para todas as portas, ou ex: '80,443'"},
                    "version_detection": {"type": "boolean", "description": "adiciona -sV"},
                    "scripts": {"type": "boolean", "description": "adiciona -sC"},
                    "purpose": {"type": "string"},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_gobuster",
            "description": (
                "Enumeracao de diretorios/arquivos web com gobuster dir. "
                "Prefira esta ferramenta a run_command para gobuster -- a sintaxe "
                "da flag e a wordlist ja vem validadas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "wordlist": {
                        "type": "string",
                        "description": "alias (small/common/big/seclists_small/seclists_medium/seclists_big) ou caminho absoluto; vazio = common",
                    },
                    "extensions": {"type": "string", "description": "ex: 'php,html,txt'"},
                    "status_codes": {"type": "string", "description": "default ja cobre os status uteis"},
                    "threads": {"type": "integer"},
                    "purpose": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_hydra",
            "description": (
                "Forca bruta de login (ssh, ftp, http-post-form, etc) com hydra. "
                "Prefira esta ferramenta a run_command para hydra -- username/userlist "
                "e password/passlist sao mutuamente exclusivos e validados em codigo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "service": {"type": "string", "description": "ex: ssh, ftp, http-post-form"},
                    "username": {"type": "string", "description": "UM usuario (-l). Use isso OU userlist, nunca os dois."},
                    "userlist": {"type": "string", "description": "caminho de arquivo com varios usuarios (-L)."},
                    "password": {"type": "string", "description": "UMA senha (-p). Use isso OU passlist, nunca os dois."},
                    "passlist": {
                        "type": "string",
                        "description": "alias (rockyou) ou caminho absoluto de wordlist de senhas (-P). vazio = rockyou",
                    },
                    "purpose": {"type": "string"},
                },
                "required": ["target", "service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_nikto",
            "description": "Varredura de vulnerabilidades web com nikto, com limite de tempo seguro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sqlmap",
            "description": "Teste de SQL injection com sqlmap, modo --batch (nao interativo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "data": {"type": "string", "description": "corpo POST, se aplicavel"},
                    "extra_flags": {"type": "string"},
                    "purpose": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Executa um comando de shell no container Kali, pra qualquer "
                "ferramenta SEM wrapper dedicado (whatweb, dirb, curl, dig, john, "
                "crackmapexec, etc). Para nmap/gobuster/hydra/nikto/sqlmap, use a "
                "ferramenta dedicada em vez desta -- evita erro de sintaxe. "
                "So funciona contra IPs dentro do escopo autorizado da VPN da THM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "comando de shell completo"},
                    "purpose": {"type": "string", "description": "por que esse comando, em 1 frase"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": "Salva um achado (credencial, porta aberta, hash, flag) nas notas persistentes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notes",
            "description": "Le todas as notas salvas até agora, pra nao repetir passo ja feito.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DISPATCH = {
    "run_command": lambda args: run_command(args.get("command", ""), args.get("purpose", "")),
    "run_nmap": lambda args: run_nmap(
        args.get("target", ""), args.get("ports", "-"),
        args.get("version_detection", True), args.get("scripts", False),
        args.get("purpose", ""),
    ),
    "run_gobuster": lambda args: run_gobuster(
        args.get("url", ""), args.get("wordlist", ""), args.get("extensions", ""),
        args.get("status_codes", "200,204,301,302,307,401,403"),
        args.get("threads", 50), args.get("purpose", ""),
    ),
    "run_hydra": lambda args: run_hydra(
        args.get("target", ""), args.get("service", ""),
        args.get("username", ""), args.get("userlist", ""),
        args.get("password", ""), args.get("passlist", ""),
        args.get("purpose", ""),
    ),
    "run_nikto": lambda args: run_nikto(args.get("url", ""), args.get("purpose", "")),
    "run_sqlmap": lambda args: run_sqlmap(
        args.get("url", ""), args.get("data", ""), args.get("extra_flags", ""),
        args.get("purpose", ""),
    ),
    "write_note": lambda args: write_note(args.get("title", ""), args.get("content", "")),
    "read_notes": lambda args: read_notes(),
}
