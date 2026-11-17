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
   (OLLAMA_KEEP_ALIVE=30m mantem varios residentes ao mesmo tempo), e
   8192 e o valor que cobre o caso real sem arriscar OOM.

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

# quantas mensagens recentes (assistant/tool/user, alem do system e da
# primeira mensagem de usuario) mantemos ao comprimir o historico numa
# retentativa -- cobre as ultimas 2-3 trocas assistant/tool.
KEEP_RECENT_MESSAGES = 6


def compress_history(messages: list[dict]) -> list[dict]:
    """Mantem a mensagem de sistema (messages[0]) + a mensagem inicial
    de usuario (messages[1]) + as ultimas KEEP_RECENT_MESSAGES
    mensagens, descartando tudo no meio. So e usado na retentativa
    apos uma falha de chamada ao Ollama, pra nao bater no mesmo
    overflow de contexto de novo."""
    head_size = 2  # system + primeira mensagem de usuario
    if len(messages) <= head_size + KEEP_RECENT_MESSAGES:
        return messages  # historico ja curto, nada pra comprimir

    head = messages[:head_size]
    tail = messages[-KEEP_RECENT_MESSAGES:]
    discarded = len(messages) - len(head) - len(tail)

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
