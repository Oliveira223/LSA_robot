# Roadmap — IA Conversacional (Texto → Interpretação → Resposta)

Objetivo final: depois que o PC transcreve a fala (Whisper), o texto não deve só ser
repetido — uma IA precisa **interpretar** e **gerar uma resposta**, que depois é
convertida em voz. Tudo isso rodando no PC ou via API, para manter a Jetson
apenas como "boca e ouvido" do robô (captura de áudio + reprodução), sem processar
IA pesada nele.

```
[Texto transcrito] → [IA interpreta e responde] → [texto de resposta] → volta pra Jetson → TTS
```

Este roadmap assume que a Fase 3 do pipeline de áudio (áudio chegando no PC) já
funciona — aqui o foco é só a parte "cérebro".

---

## Fase 0 — Definir onde a IA vai rodar

Antes de codar, decida a estratégia (dá pra trocar depois, mas evita retrabalho):

| Opção | Prós | Contras |
|---|---|---|
| **API (ex: Claude, GPT)** | Zero custo de hardware, respostas de alta qualidade, fácil de trocar de modelo | Depende de internet, custo por uso, latência de rede extra |
| **Modelo local no PC** (ex: Llama/Mistral via Ollama) | Funciona offline, sem custo por request | Precisa de PC com GPU decente pra ser rápido, qualidade pode ser inferior |
| **Híbrido** | Local pra respostas simples/rápidas, API pra perguntas complexas | Mais complexo de implementar |

Para a Jetson ficar o mais leve possível, ela **nunca** deve rodar o modelo — só
manda texto e recebe texto. Essa decisão é só sobre onde a IA roda: no PC ou na nuvem.

✅ **Critério de sucesso:** decisão tomada e anotada (pode começar com API pra prototipar rápido, e migrar pra local depois se quiser independência de internet).

---

## Fase 1 — Resposta "fake" fixa (validar o encaixe no pipeline)

**Objetivo:** confirmar que dá pra plugar uma "camada de IA" no meio do pipeline sem quebrar nada, antes de gastar tempo com IA de verdade.

- [ ] No PC, criar uma função `gerar_resposta(texto) -> texto` que só devolve algo fixo ou um eco
- [ ] Conectar essa função entre a transcrição (Whisper) e o envio de volta pra Jetson

```python
def gerar_resposta(texto_usuario: str) -> str:
    return f"Você disse: {texto_usuario}. Ainda estou aprendendo a responder de verdade."
```

✅ **Critério de sucesso:** o pipeline completo (fala → Whisper → função fake → texto → Jetson fala) funciona de ponta a ponta.

---

## Fase 2 — Regras simples (sem IA generativa ainda)

**Objetivo:** ter algumas respostas "inteligentes" básicas antes de subir a complexidade, útil pra comandos fixos do robô (ex: "olá", "gire a cabeça", "qual seu nome").

- [ ] Criar um dicionário de intenções simples (keyword matching)
- [ ] Testar comandos fixos que o robô deve reconhecer sempre, mesmo sem internet/API

```python
REGRAS = {
    "oi": "Olá! Eu sou o robô.",
    "seu nome": "Meu nome é... (defina aqui)",
    "desligar": "Até logo!",
}

def gerar_resposta(texto: str) -> str:
    texto = texto.lower()
    for chave, resposta in REGRAS.items():
        if chave in texto:
            return resposta
    return None  # sinaliza que precisa cair pra IA generativa (fase seguinte)
```

✅ **Critério de sucesso:** comandos fixos respondem instantaneamente, sem depender de API/internet.

---

## Fase 3 — IA generativa via API (prototipagem rápida)

**Objetivo:** plugar uma IA de verdade, sem se preocupar ainda com custo/latência/offline.

- [ ] Criar conta e chave de API do provedor escolhido
- [ ] Fazer uma chamada simples texto → texto
- [ ] Definir um **system prompt** que dá personalidade ao robô (nome, tom de voz, limites do que ele sabe/faz)
- [ ] Integrar como "fallback" das regras da Fase 2 (se não bater nenhuma regra, chama a API)

```python
import anthropic

client = anthropic.Anthropic(api_key="SUA_CHAVE")

SYSTEM_PROMPT = """Você é um robô assistente, responde de forma curta e direta,
em português, com um tom levemente bem-humorado. Respostas devem ter no máximo
2 frases, pois serão faladas em voz alta."""

def gerar_resposta_ia(texto_usuario: str) -> str:
    resposta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": texto_usuario}]
    )
    return resposta.content[0].text
```

⚠️ **Ponto de atenção:** respostas de IA generativa tendem a ser longas — force
respostas curtas no prompt (como no exemplo acima), já que tudo vai ser
sintetizado em voz e respostas longas ficam cansativas de ouvir.

✅ **Critério de sucesso:** pergunta livre (não coberta pelas regras) recebe uma resposta coerente da IA, dentro de um tempo aceitável (poucos segundos).

---

## Fase 4 — Memória de conversa (contexto entre falas)

**Objetivo:** o robô lembrar o que foi dito antes na mesma "sessão", em vez de tratar cada frase isoladamente.

- [ ] Manter uma lista de mensagens (histórico) em memória, no PC, enquanto o robô estiver "ligado"
- [ ] Adicionar cada pergunta/resposta ao histórico e mandar o histórico completo a cada chamada de API
- [ ] Definir quando o histórico deve ser limpo (ex: comando de voz "esquece tudo", ou depois de X minutos de silêncio)

```python
historico = []

def gerar_resposta_ia(texto_usuario: str) -> str:
    historico.append({"role": "user", "content": texto_usuario})
    resposta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        system=SYSTEM_PROMPT,
        messages=historico
    )
    texto_resposta = resposta.content[0].text
    historico.append({"role": "assistant", "content": texto_resposta})
    return texto_resposta
```

⚠️ **Ponto de atenção:** histórico infinito custa mais tokens (e dinheiro, se for
API paga) a cada chamada. Considere truncar o histórico às últimas N trocas.

✅ **Critério de sucesso:** você pergunta algo, depois faz uma pergunta que depende do contexto anterior ("e o dobro disso?"), e o robô responde corretamente.

---

## Fase 5 — Avaliar modelo local (opcional, para reduzir dependência de internet/custo)

**Objetivo:** ter uma alternativa que funcione sem internet ou sem custo por request, caso isso vire prioridade.

- [ ] Instalar [Ollama](https://ollama.com) no PC
- [ ] Baixar um modelo leve (ex: `llama3.2` ou `mistral`) compatível com o hardware do PC
- [ ] Trocar a chamada de API pela chamada local, mantendo a mesma função `gerar_resposta_ia`
- [ ] Comparar qualidade de resposta e velocidade contra a versão via API

```bash
ollama pull llama3.2
```

```python
import requests

def gerar_resposta_ia(texto_usuario: str) -> str:
    r = requests.post("http://localhost:11434/api/generate", json={
        "model": "llama3.2",
        "prompt": f"{SYSTEM_PROMPT}\n\nUsuário: {texto_usuario}",
        "stream": False
    })
    return r.json()["response"]
```

✅ **Critério de sucesso:** modelo local responde com qualidade aceitável e velocidade compatível com uma conversa falada (idealmente poucos segundos).

---

## Fase 6 — Roteador de estratégia (híbrido, opcional)

**Objetivo:** combinar o melhor dos dois mundos — respostas rápidas/fixas quando possível, IA pesada só quando necessário.

- [ ] Ordem de prioridade sugerida: regras fixas (Fase 2) → modelo local (Fase 5) → API (Fase 3) como fallback de qualidade
- [ ] Definir critério de quando usar cada um (ex: perguntas curtas/comandos = regras; perguntas abertas = IA)
- [ ] Medir a latência de cada caminho pra garantir que a conversa não fique "travada"

---

## Resumo

| Fase | Valida |
|---|---|
| 0 | Decisão de onde a IA roda (API, local ou híbrido) |
| 1 | Encaixe de uma "camada de IA" fake no pipeline |
| 2 | Respostas fixas por regras (sem depender de internet) |
| 3 | IA generativa via API, com personalidade definida |
| 4 | Memória de conversa (contexto entre falas) |
| 5 | Alternativa local via Ollama (offline / sem custo) |
| 6 | Estratégia híbrida combinando regras + local + API |

**Lembrete central:** em todas as fases, a Jetson só manda texto e recebe texto — a IA nunca roda nela. Isso mantém o robô leve e a "inteligência" centralizada e fácil de trocar/melhorar sem mexer no hardware da cabeça.