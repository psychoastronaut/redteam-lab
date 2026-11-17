"""
Estrutura base dos playbooks: um playbook e so um roteiro de fases
SUGERIDAS que a gente injeta na mensagem inicial do planner
(orchestrator.py) pra dar um ponto de partida mais calibrado pro tipo
de sala do TryHackMe, sem tirar do planner a decisao de pular fase,
mudar ordem ou ignorar tecnica que nao se aplica.

Nenhum playbook executa nada sozinho -- e so texto formatado que entra
na mensagem de usuario do planner. O planner continua so podendo
chamar dispatch_subtask/read_notes/conclude, como sempre.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class Phase:
    """Uma fase do playbook: nome curto, objetivo em 1 frase, e uma
    lista de tecnicas/ideias concretas que o worker pode tentar."""

    name: str
    objective: str
    techniques: List[str] = field(default_factory=list)


class BasePlaybook:
    """Classe base de playbook. Subclasses so precisam definir `name`,
    `description` e `phases` -- get_task() monta o texto final."""

    name: str = "base_playbook"
    description: str = "Playbook base (nao deveria ser usado direto)"
    phases: List[Phase] = []

    def get_task(self) -> str:
        """Monta o texto do playbook pronto pra entrar na mensagem do
        planner. Formato simples (fase / objetivo / tecnicas numeradas)
        pra ficar facil do modelo seguir sem parsing nenhum."""
        task = f"{self.description}\n\n"
        for phase in self.phases:
            task += f"Fase: {phase.name}\n"
            task += f"Objetivo: {phase.objective}\n"
            task += "Tecnicas sugeridas:\n"
            for i, technique in enumerate(phase.techniques, 1):
                task += f"  {i}. {technique}\n"
            task += "\n"
        return task
