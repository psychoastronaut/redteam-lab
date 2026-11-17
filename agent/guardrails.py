"""
Freio de mão do agente.

Duas camadas:
1. Escopo: qualquer IP mencionado no comando tem que estar dentro de
   ALLOWED_TARGET (a sub-rede VPN da THM, por padrao 10.64.0.0/12 --
   confirme a rota real do seu tun0 com `ip route`, a THM ja mudou essa
   faixa antes e pode mudar de novo).
   Se o comando nao menciona IP nenhum (ls, cat, etc), passa direto.
2. Blocklist: padroes que nao tem nada a ver com pentest e so servem
   pra quebrar o container, a VPN ou vazar coisa do host.
"""

import ipaddress
import re

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/(?!\S)",          # rm -rf / (raiz, nao um path qualquer)
    r"rm\s+-rf\s+/\*",
    r"mkfs\.",
    r"dd\s+if=.*of=/dev/(sd|nvme|vd)",
    r":\(\)\{\s*:\|:&\s*\};:",      # fork bomb
    r"\bshutdown\b",
    r"\breboot\b",
    r"\binit\s+0\b",
    r">\s*/dev/sd[a-z]",
    r"chmod\s+-R\s+777\s+/(?!\S)",
    r"/etc/passwd.*>\s*/dev",
]

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")


class GuardrailViolation(Exception):
    pass


def check_scope(command: str, allowed_cidr: str) -> None:
    """Levanta GuardrailViolation se o comando referenciar IP fora do escopo."""
    network = ipaddress.ip_network(allowed_cidr, strict=False)
    for match in _IP_RE.findall(command):
        candidate = match.split("/")[0]
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if ip not in network:
            raise GuardrailViolation(
                f"IP {candidate} esta fora do escopo autorizado ({allowed_cidr}). "
                f"Comando bloqueado."
            )


def check_dangerous(command: str) -> None:
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            raise GuardrailViolation(
                f"Comando bate com padrao destrutivo bloqueado: {pattern!r}"
            )


def validate(command: str, allowed_cidr: str) -> None:
    check_dangerous(command)
    check_scope(command, allowed_cidr)
