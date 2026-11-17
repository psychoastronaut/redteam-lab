"""
Playbook de exploracao de aplicacao web, calibrado pra maquinas do
estilo TryHackMe (ex: Pickle Rick, um site/CMS/API exposto numa porta
80/443/8080) -- nao e o thp3_web/thp3_recon do projeto original, que
pensa em superficie corporativa grande. Aqui fundimos recon web +
exploracao em duas fases so, porque numa sala THM normal o alvo e
"uma aplicacao" e nao "uma frota de aplicacoes".
"""

from .base import BasePlaybook, Phase


class WebExploitationPlaybook(BasePlaybook):
    name = "web"
    description = (
        "Exploracao de aplicacao web numa maquina THM. Foco em achar a "
        "stack, mapear o que esta exposto e explorar ate conseguir "
        "execucao de comando ou credencial que leve a um shell."
    )
    phases = [
        Phase(
            name="Discovery",
            objective="Mapear a superficie de ataque da aplicacao web",
            techniques=[
                "Varredura de portas e servicos (nmap -sV -sC) alem do 80/443 -- muita sala THM esconde painel em porta alternativa",
                "Identificar tecnologia/CMS/framework (whatweb, headers HTTP, favicon, /robots.txt, /sitemap.xml)",
                "Enumeracao de diretorios e arquivos (gobuster dir) com wordlist comum, depois seclists se nada aparecer",
                "Ler o HTML/JS servido a procura de comentarios, endpoints de API, chaves ou caminhos vazados",
                "Mapear pontos de entrada (formularios, parametros de URL, upload de arquivo, cookies/sessao)",
                "Checar arquivos sensiveis comuns (wp-config.php, .git/, backup.zip, config.php.bak)",
            ],
        ),
        Phase(
            name="Exploitation",
            objective="Explorar vulnerabilidade encontrada ate obter acesso (shell, credencial, arquivo sensivel)",
            techniques=[
                "Testar credenciais default/fracas no painel de login achado (admin:admin, etc) antes de forca bruta pesada",
                "Injecao de SQL em parametros suspeitos (sqlmap --batch) se houver form/endpoint com entrada de usuario",
                "Local/Remote File Inclusion e path traversal em parametros que parecem caminho de arquivo",
                "Upload de arquivo malicioso se houver campo de upload sem validacao de tipo",
                "Vulnerabilidade conhecida da versao do CMS/framework identificada na fase anterior (buscar CVE/exploit publico)",
                "Command injection em campos que parecem disparar comando no servidor",
                "Bypass de autenticacao/autorizacao (IDOR, manipulacao de cookie/sessao, forced browsing pra rota admin)",
                "Depois de achar credencial ou shell web, escrever a credencial/caminho em write_note antes de seguir",
            ],
        ),
    ]
