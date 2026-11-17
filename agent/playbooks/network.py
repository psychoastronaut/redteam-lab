"""
Playbook de comprometimento de rede/host, calibrado pra escala THM:
UMA maquina atras da VPN, geralmente Linux, com um servico de rede
(SSH, FTP, SMB, servico customizado) como porta de entrada e escalada
de privilegio LOCAL no final (SUID, cron, kernel, sudo mal configurado)
-- nao e dominio AD corporativo com Lateral Movement entre dezenas de
hosts, que e o cenario do thp3_network original.
"""

from .base import BasePlaybook, Phase


class NetworkCompromisePlaybook(BasePlaybook):
    name = "network"
    description = (
        "Comprometimento de host THM via servico de rede. Foco em "
        "ganhar um shell de usuario comum primeiro e depois escalar "
        "privilegio localmente na mesma maquina (sem rede corporativa "
        "nem dominio AD envolvido)."
    )
    phases = [
        Phase(
            name="Initial Access",
            objective="Ganhar o primeiro shell/acesso de usuario comum na maquina",
            techniques=[
                "Varredura completa de portas e versao de servico (nmap -p- -sV -sC)",
                "Enumerar versao exata de cada servico exposto (SSH, FTP, SMB, etc) e procurar exploit publico conhecido pra aquela versao",
                "Testar login anonimo/sem senha em FTP e SMB antes de qualquer forca bruta",
                "Forca bruta de credencial (hydra) so depois de ja ter um usuario conhecido (de recon, de arquivo publico, de nome da sala/historia)",
                "Testar reuso de credencial achada em outro servico/fase (ex: senha de um form web no SSH)",
                "Explorar vulnerabilidade de aplicacao custom na porta nao-padrao, se existir, como vetor de entrada",
            ],
        ),
        Phase(
            name="Enumeration & Privesc",
            objective="Depois do primeiro shell, levantar o sistema e escalar pra root/admin",
            techniques=[
                "Levantar usuario atual, grupos, sudo -l, e capacidades (id, sudo -l, getcap -r /)",
                "Procurar binarios SUID fora do padrao (find / -perm -4000 -type f 2>/dev/null) e checar contra GTFOBins",
                "Checar cron jobs e scripts rodados por root (/etc/cron*, crontab -l) por arquivo gravavel pelo usuario atual",
                "Checar versao do kernel/SO pra exploit local conhecido, so depois de esgotar vetor de configuracao (mais confiavel que kernel exploit)",
                "Procurar credencial/chave SSH/arquivo de config com senha em texto claro no sistema de arquivos (history, .bash_history, arquivos de config de app)",
                "Checar processos rodando como root e servicos internos (netstat/ss -tulpn) que o usuario atual pode abusar",
                "Escrever toda credencial e caminho achado em write_note antes de tentar escalar de fato",
            ],
        ),
        Phase(
            name="Lateral Movement",
            objective="So se o objetivo exigir mais de um host (raro em sala THM de maquina unica) ou houver pivot claro",
            techniques=[
                "Checar se a maquina tem acesso a outra rede/interface (ip a, /etc/hosts, segunda NIC) que indique pivot",
                "Reusar credencial achada contra outro host visivel na rede interna, se existir",
                "Pular esta fase inteira se a sala for de host unico sem sinal de segunda rede -- normalmente e o caso em THM",
            ],
        ),
    ]
