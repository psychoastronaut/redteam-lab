# Lab de redteam local (TryHackMe)

Dois containers: `redteam-ollama` (modelo local, GPU) e `redteam-kali`
(ferramentas de pentest + o agente Python). O agente fala com o Ollama
pela rede interna do compose e executa comandos dentro do próprio
container Kali -- nunca no host.

## Subir o ambiente

    cd ~/seclab/redteam
    docker compose up -d --build
    docker exec redteam-ollama ollama pull qwen2.5:14b-instruct

A primeira build baixa a imagem do Kali e instala as ferramentas (uns
5-10 min). O pull do modelo são ~9 GB.

## Conectar na VPN da THM

Ver `vpn/README.md`. Resumo: baixe o `.ovpn` da sua conta, coloque em
`vpn/thm.ovpn`, conecte de dentro do container com `openvpn --config
/vpn/thm.ovpn --daemon`.

## Rodar o agente

    docker exec -it redteam-kali python3 /agent/agent.py \
        --target 10.10.123.45 \
        --goal "pegar user.txt"

Por padrão (`EXEC_MODE=confirm`) o agente mostra cada comando antes de
rodar e espera você digitar `y`. Pra deixar rodando sozinho numa
máquina que você já conhece:

    EXEC_MODE=auto docker compose up -d kali
    docker exec -it redteam-kali python3 /agent/agent.py --target ... --goal ...

## Guardrails

- `agent/guardrails.py` bloqueia qualquer comando que mencione um IP
  fora de `ALLOWED_TARGET` (padrão `10.64.0.0/12`, que é a faixa atual
  roteada pela VPN da THM -- confirme com `ip route` depois de
  conectar, porque a THM já trocou essa faixa antes e pode trocar de
  novo). Se vier diferente, ajuste no `.env` (ver `.env.example`).
- Bloqueia também um punhado de padrões destrutivos (rm -rf /, dd em
  disco, fork bomb, shutdown) -- isso é pra não vazar efeito pro host,
  não é censura de técnica de ataque.
- Todo comando proposto, executado ou bloqueado vai pra
  `workspace/log.jsonl`. Achados relevantes o próprio agente salva em
  `workspace/notes.md` via a ferramenta `write_note`.

## Painel web (sem precisar de terminal)

Abra **http://localhost:8088** no navegador. Dá pra escolher target,
objetivo e modo (`agent` monolítico ou `orchestrator`), acompanhar o
log em tempo real e apertar "parar".

Garantias de confiabilidade:
- **Um job por vez, de verdade** -- uma segunda tentativa de iniciar
  enquanto outra roda é recusada (`409`), não enfileirada nem
  disparada por cima. Foi exatamente o bug de dois processos
  duplicados que pegamos hoje testando na mão.
- **Timeout automático** (`WEBUI_MAX_RUNTIME`, default 1800s = 30 min)
  -- se o agente ficar preso, o job é matado e marcado como `timeout`
  em vez de rodar pra sempre consumindo GPU sem ninguém notar.
- **Escopo validado antes de iniciar** -- um alvo fora de
  `ALLOWED_TARGET` nem chega a disparar processo.
- **Supervisor no entrypoint** -- se o próprio servidor web cair, ele
  religa em 2s (log em `workspace/webui.log`).
- Qualquer exceção no job cai no campo `error` do status, nunca trava
  a página silenciosamente.

## Modo orquestrador (planner grande + workers leves)

`agent.py` é o loop monolítico: um modelo só faz tudo. `orchestrator.py`
separa em duas camadas -- um **planner** (modelo maior) que só decide
a próxima fase e nunca toca ferramenta, e um **worker** (modelo leve,
reiniciado a cada subtask) que executa com o mesmo `tools.py` e
`guardrails.py` de sempre. O planner só vê o resumo de 2-4 frases que
o worker devolve, nunca a saída crua de nmap/gobuster -- é isso que
mantém o contexto do modelo caro pequeno e as decisões rápidas.

    docker exec redteam-ollama ollama pull qwen2.5:7b-instruct
    docker exec redteam-ollama ollama pull qwen2.5:3b-instruct
    docker exec -it redteam-kali python3 /agent/orchestrator.py \
        --target 10.67.177.103 \
        --goal "pegar user.txt"

`7b` (planner) + `3b` (worker) somam ~6,7 GB de VRAM, cabem residentes
ao mesmo tempo na 3060 sem troca de modelo. Se quiser um planner mais
forte (`14b`, ~9 GB) o Ollama troca o modelo em memória a cada chamada
em vez de manter os dois -- funciona, só adiciona ~10-20s de latência
por troca. Ajuste via `PLANNER_MODEL` / `WORKER_MODEL` / `WORKER_MAX_STEPS`
/ `PLANNER_MAX_PHASES` no `.env`.

Bom material de eval, por sinal: compare `agent.py` (monolítico) contra
`orchestrator.py` no mesmo alvo e meça passos até a flag, tokens
gastos e tempo total -- é exatamente o tipo de comparação que mostra
se separar em camadas vale o custo extra de latência.

## Trocar o modelo

`qwen2.5:14b-instruct` quantizado cabe com folga nos 12 GB da 3060 e
suporta tool-calling nativo no Ollama. Pra algo mais rápido em troca de
raciocínio mais fraco em exploração encadeada:

    OLLAMA_MODEL=qwen2.5:7b-instruct docker compose up -d kali
    docker exec redteam-ollama ollama pull qwen2.5:7b-instruct

## Ferramentas instaladas no Kali

nmap, gobuster, nikto, sqlmap, hydra, john, whatweb, dirb, seclists,
netcat, dig, curl, wget, openvpn. Metasploit não entrou (pesado e lento
de instalar) -- se precisar, `docker exec -it redteam-kali apt install
metasploit-framework`.

## Apoie o projeto

Se o lab te ajudou, você pode me pagar um café:

[![Buy Me A Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://buymeacoffee.com/rodrigomcaq)
