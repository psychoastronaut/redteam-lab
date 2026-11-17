"""
Registro de playbooks disponiveis, pra lookup por nome (string) vindo
do --playbook do orchestrator.py ou do <select> do webui.py.
"""

from .base import BasePlaybook, Phase
from .web import WebExploitationPlaybook
from .network import NetworkCompromisePlaybook

PLAYBOOKS = {
    WebExploitationPlaybook.name: WebExploitationPlaybook,
    NetworkCompromisePlaybook.name: NetworkCompromisePlaybook,
}

__all__ = ["BasePlaybook", "Phase", "PLAYBOOKS"]
