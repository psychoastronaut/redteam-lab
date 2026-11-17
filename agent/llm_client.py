"""
Cliente compartilhado pra chamadas ao Ollama /api/chat.

Resolve dois problemas vistos em sessao real (log do container
redteam-ollama):

1. Sem "num_ctx" nas options, o Ollama usa o default do modelo
   (4096 tokens). O loop de agente acumula TODO o historico da
   conversa -- incluindo saida de nmap/nikto/gobuster, ja truncada a
   8000 chars cada em tools.py -- e por volta do passo 6-7 de uma
   sessao real isso passa de 4096 tokens. Quando isso acontece, o
   llama.cpp faz um "context shift" NO MEIO da geracao (descarta um
   bloco do meio do contexto pra abrir espaco), o que corrompe a
   resposta e o servidor devolve 500 pro /api/chat -- matando a sessao
   inteira, porque agent.py/worker.py/orchestrator.py tratavam isso
   como erro fatal.

   OLLAMA_NUM_CTX (default 8192, o dobro do default do modelo) reduz
   bastante a chance disso acontecer numa sessao normal. Nao subimos
   mais que isso sem motivo: a GPU e uma RTX 3060 de 12GB com dois
   modelos de 7B residentes (~9,4GB juntos) -- cada 1k de contexto
   extra custa VRAM de KV cache multiplicada pelos modelos carregados
   (OLLAMA_KEEP_ALIVE=30m ajuda varios ficarem residentes ao mesmo
   tempo, mas nao garante isso sob pressao de memoria -- o Ollama pode
   descarregar um modelo mais cedo se precisar de VRAM), e 8192 e o
   valor que cobre o caso real sem arriscar OOM.

2. Mesmo com num_ctx maior, uma sessao longa o bastante ainda pode
   estourar o contexto (ou a chamada pode falhar por outro motivo
   transiente de rede/servidor). Em vez de abortar a sessao inteira na
   primeira falha, chat_with_retry tenta de novo UMA vez, comprimindo
   o historico antes (mantendo system + primeira mensagem de usuario +
   as trocas mais recentes, descartando o meio). Se a retentativa
   tambem falhar, a excecao original e propagada pra quem chamou
   decidir como encerrar a sessao.
"""

import os

import requests

OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# quantos TURNOS completos recentes mantemos ao comprimir o historico
# numa retentativa. Um turno e uma mensagem assistant + todas as
# mensagens que vieram depois dela (tool results, o aviso de "voce so
# narrou" que worker.py injeta como role=user, etc) ate o proximo
# assistant -- cobre aproximadamente as ultimas 2-3 trocas.
KEEP_RECENT_TURNS = 3


def _split_into_turns(rest: list[dict]) -> list[list[dict]]:
    """Agrupa uma lista de mensagens (sem o head) em turnos completos.
    Um turno comeca numa mensagem role=assistant e inclui todas as
    mensagens seguintes (role=tool, ou o aviso extra role=user que
    worker.py injeta quando o modelo so narra sem chamar ferramenta)
    ate a proxima mensagem assistant.

    Isso existe porque o padrao de mensagens NAO e estritamente
    alternado assistant/tool: worker.py intercala um aviso role=user
    fora de ordem, e um assistant pode ter varios tool_calls em
    paralelo (varias mensagens role=tool seguidas). Cortar por
    contagem fixa de mensagens pode deixar uma mensagem role=tool
    orfa (sem o assistant com o tool_call correspondente antes dela)
    no inicio do trecho mantido -- agrupar por turno evita isso."""
    turns: list[list[dict]] = []
    current: list[dict] = []
    for m in rest:
        if m.get("role") == "assistant":
            if current:
                turns.append(current)
            current = [m]
        else:
            # mensagem de continuacao do turno atual (tool result, aviso
            # de narracao etc). Se vier antes de qualquer assistant (nao
            # deveria acontecer na pratica), fica num turno "orfao"
            # proprio -- ele sempre sera o mais antigo, entao e o primeiro
            # a ser descartado quando ha compressao real.
            current.append(m)
    if current:
        turns.append(current)
    return turns


def compress_history(messages: list[dict]) -> list[dict]:
    """Mantem a mensagem de sistema (messages[0]) + a mensagem inicial
    de usuario (messages[1]) + os ultimos KEEP_RECENT_TURNS turnos
    completos, descartando tudo no meio. So e usado na retentativa
    apos uma falha de chamada ao Ollama, pra nao bater no mesmo
    overflow de contexto de novo.

    Corta por TURNO (assistant + tool results/avisos seguintes), nunca
    por contagem bruta de mensagens, pra garantir que o trecho mantido
    sempre comeca com um assistant -- nunca com uma mensagem role=tool
    orfa, sem o tool_call correspondente antes dela."""
    head_size = 2  # system + primeira mensagem de usuario
    if len(messages) <= head_size:
        return messages

    head = messages[:head_size]
    rest = messages[head_size:]
    turns = _split_into_turns(rest)

    if len(turns) <= KEEP_RECENT_TURNS:
        return messages  # historico ja curto, nada pra comprimir

    kept_turns = turns[-KEEP_RECENT_TURNS:]
    discarded_turns = turns[:-KEEP_RECENT_TURNS]
    discarded = sum(len(t) for t in discarded_turns)
    tail = [m for turn in kept_turns for m in turn]

    notice = {
        "role": "user",
        "content": (
            f"[aviso do sistema: {discarded} mensagens do meio desta conversa "
            "foram descartadas para liberar espaco de contexto, apos uma falha "
            "na chamada ao modelo. Continue a partir das mensagens recentes "
            "abaixo -- se precisar relembrar achados antigos, use read_notes.]"
        ),
    }
    return head + [notice] + tail


def chat_with_retry(
    host: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    timeout: int = 300,
) -> tuple[dict, list[dict]]:
    """Chama POST {host}/api/chat. Se a chamada falhar (HTTPError,
    timeout, erro de conexao etc), comprime o historico e tenta mais
    uma vez. Retorna (resposta_json, messages_efetivamente_usada) --
    messages_efetivamente_usada e o historico comprimido quando houve
    retentativa, ou o original sem mudanca nenhuma. Quem chamar deve
    continuar usando o historico retornado (nao o original) daqui pra
    frente, senao a compressao se perde no proximo turno.

    Propaga requests.RequestException se a retentativa tambem falhar --
    quem chamar decide como encerrar a sessao nesse caso."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"num_ctx": OLLAMA_NUM_CTX},
    }
    try:
        resp = requests.post(f"{host}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), messages
    except requests.RequestException as e:
        print(
            f"[llm_client] chamada ao Ollama falhou ({e}); comprimindo "
            "historico e tentando mais uma vez..."
        )
        compressed = compress_history(messages)
        payload["messages"] = compressed
        resp = requests.post(f"{host}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), compressed
