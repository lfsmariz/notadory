# Notadory Memory MCP

Servidor MCP por **stdio** para guardar e recuperar memórias duráveis em
arquivos JSON locais. A V1 usa um léxico determinístico: não usa embeddings,
LLM nem banco de dados durante a busca.

Ele é destinado a harnesses de agentes, como OpenCode, Claude Code e Codex.
O harness decide quando chamar as ferramentas; o servidor valida, persiste e
consulta as memórias.

## O que a V1 faz

- armazena memórias globais e por projeto em JSON, com gravação atômica;
- associa memórias a `concept_id`s estáveis;
- busca aliases e pares de termos de ação/objeto de forma determinística;
- evita duplicatas por conteúdo normalizado e revisa registros pelo mesmo ID;
- retorna apenas contexto novo ou revisado em cada conversa;
- permite ampliar um conceito com novos termos sem regravar memórias antigas.

Fora do escopo: embeddings, score automático, banco relacional, análise
semântica por modelo e reindexação que altere ou apague registros.

## Pré-requisitos e instalação

- Python 3.10 ou superior;
- [uv](https://docs.astral.sh/uv/) instalado.

```sh
git clone <url-do-repositorio> /caminho/notadory
cd /caminho/notadory
uv sync --extra dev
uv run pytest
```

O comando do servidor é:

```sh
uv run notadory-memory-mcp
# equivalente:
uv run python -m notadory_memory.server
```

Ele usa stdio para o protocolo MCP. Nunca escreva logs ou mensagens de
diagnóstico em stdout; esse canal pertence ao protocolo.

## Dados locais

Por padrão, os dados ficam em `memory-data/` relativo ao diretório de execução.
Para garantir a localização, especialmente em configurações de harness,
configure `MEMORY_DATA_DIR` com um caminho absoluto.

```text
memory-data/
  lexicon/
    concepts.json
    project-overrides/
      <project-id>.json
  memories/
    global/
      mem-<id>.json
    projects/
      <project-id>/
        mem-<id>.json
  cache/
    conversations/
      <hash>.json
```

Os arquivos de `memories/` são a fonte de verdade. O cache é descartável,
separado por conversa, tem TTL e limita entradas entregues por conversa.

## Configurar um harness

Substitua `/caminho/notadory` pelo caminho absoluto deste repositório. Em todos
os exemplos, o processo recebe `MEMORY_DATA_DIR` para separar seus dados do
código e deve ser reiniciado depois de mudar a configuração.

### OpenCode

Crie ou complete `opencode.json` (ou `opencode.jsonc`) na raiz do projeto para
uma configuração local, ou `~/.config/opencode/opencode.json` para uma
configuração global:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "notadory-memory": {
      "type": "local",
      "command": [
        "uv",
        "run",
        "--directory",
        "/caminho/notadory",
        "notadory-memory-mcp"
      ],
      "environment": {
        "MEMORY_DATA_DIR": "/caminho/notadory/memory-data"
      },
      "enabled": true
    }
  }
}
```

No OpenCode, `command` é uma lista: cada argumento precisa ocupar um item.
Consulte a [documentação de MCP do OpenCode](https://opencode.ai/docs/mcp-servers/).

### Claude Code

Crie `.mcp.json` na raiz do projeto para compartilhar a configuração do
repositório, ou registre o servidor globalmente em `~/.claude.json`:

```json
{
  "mcpServers": {
    "notadory-memory": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/caminho/notadory",
        "notadory-memory-mcp"
      ],
      "env": {
        "MEMORY_DATA_DIR": "/caminho/notadory/memory-data"
      }
    }
  }
}
```

Alternativamente:

```sh
claude mcp add --transport stdio notadory-memory -- \
  uv run --directory /caminho/notadory notadory-memory-mcp
```

Consulte a [documentação MCP do Claude Code](https://docs.anthropic.com/en/docs/claude-code/mcp).

### Codex CLI

Adicione ao `~/.codex/config.toml` para uso global ou a
`.codex/config.toml` do projeto confiável:

```toml
[mcp_servers.notadory-memory]
command = "uv"
args = [
  "run",
  "--directory",
  "/caminho/notadory",
  "notadory-memory-mcp",
]

[mcp_servers.notadory-memory.env]
MEMORY_DATA_DIR = "/caminho/notadory/memory-data"
```

Ou registre via CLI:

```sh
codex mcp add notadory-memory -- \
  uv run --directory /caminho/notadory notadory-memory-mcp
```

Consulte a [documentação MCP do Codex](https://developers.openai.com/codex/mcp).

## Ferramentas

| Ferramenta | Finalidade |
| --- | --- |
| `create_concept` | Cria um conceito global ou específico de projeto. |
| `append_concept_terms` | Acrescenta aliases, ações ou objetos a um conceito existente. |
| `create_memory` | Cria, deduplica ou revisa uma memória. |
| `list_memories` | Lista resumos paginados, sem `content`. |
| `get_memory` | Retorna metadados e resumo, sem `content`. |
| `load_memory` | Carrega o conteúdo de uma memória no escopo informado. |
| `retrieve_context` | Busca contexto relevante de níveis 1–4 e aplica cache por conversa. |
| `reindex_archive` | Lista candidatos ativos de nível 5, sem modificá-los. |

Todas as ferramentas retornam dados estruturados pelo MCP. Erros de validação,
escopo ou conflito são retornados ao harness como falha da chamada.

## Escopos

O escopo é sempre explícito; o servidor não deduz o projeto pelo texto.

```json
{"type": "global"}
```

```json
{"type": "project", "project_id": "payments-api"}
```

Uma busca de projeto considera memórias globais e daquele projeto. `load_memory`
e `get_memory`, porém, carregam somente o arquivo pertencente exatamente ao
escopo solicitado.

## Fluxo recomendado para o agente

1. Antes de implementar algo que dependa de convenções ou decisões, chame
   `retrieve_context` com o escopo, a tarefa atual e uma `conversation_key`
   estável fornecida pelo harness.
2. Use as memórias retornadas como contexto, mas confira o código atual antes
   de editar. Se houver divergência, sinalize-a.
3. Crie uma memória somente para fatos reutilizáveis: decisão, convenção,
   preferência estável ou correção que muda respostas futuras.
4. Quando uma expressão nova representar um conceito existente, use
   `append_concept_terms`; as memórias antigas já associadas ao mesmo ID passam
   a ser encontradas por ela.
5. Crie um conceito com `create_concept` apenas quando for um conceito novo e
   durável. Tecnologia ou preferência exclusiva de um projeto geralmente é uma
   memória de projeto, não um alias global.

Não salve transcrições, conversas inteiras ou tarefas transitórias. A consulta
normal não carrega nível 5.

### Criar e ampliar um conceito

```json
{
  "concept_id": "operation.input_validation",
  "label": "Validação de entrada",
  "aliases": ["validar o payload", "valide a entrada"],
  "action_terms": ["validar", "verificar"],
  "object_terms": ["entrada", "payload"],
  "scope": {"type": "global"}
}
```

Mais tarde, acrescente novos termos ao mesmo conceito sem tocar nas memórias:

```json
{
  "concept_id": "operation.input_validation",
  "aliases": ["checagem do corpo"],
  "scope": {"type": "global"}
}
```

`append_concept_terms` retorna `updated` se incluiu algo e `deduplicated` se os
termos já existiam. Em escopo de projeto, seus termos ficam no override desse
projeto e não alteram o léxico global.

### Criar uma memória

```json
{
  "scope": {"type": "project", "project_id": "payments-api"},
  "title": "Validação de entrada nas rotas",
  "summary": "As rotas HTTP validam o corpo com Zod.",
  "content": "Reutilize os schemas de domínio em src/schemas antes de executar a lógica da rota.",
  "concept_ids": ["operation.input_validation"],
  "context": {"transport": "http", "technology": "zod"},
  "tier": 2,
  "status": "active",
  "provenance": {"kind": "project_decision", "source_ref": "ADR-004"}
}
```

O servidor gera `id`, timestamps, `revision` e `content_hash`. O mesmo conteúdo
normalizado no mesmo escopo é deduplicado; reutilizar o mesmo ID com conteúdo
diferente cria uma revisão.

### Recuperar contexto

```json
{
  "scope": {"type": "project", "project_id": "payments-api"},
  "conversation_key": "host-session-42",
  "query": "validar o payload HTTP",
  "context": {"transport": "http"},
  "limit": 8
}
```

`retrieve_context` aplica normalização Unicode, remove acentos e palavras
funcionais, verifica aliases e combina pistas de ação/objeto. Sem correspondência
suficiente, retorna zero resultados. O conteúdo é enviado apenas para memórias
novas ou revisadas; as já entregues aparecem em `already_delivered`. Use
`force_reload: true` apenas quando precisar receber o conteúdo novamente.

## Desenvolvimento e verificação

```sh
uv sync --extra dev
uv run pytest

# smoke test: encerra corretamente quando stdin fecha e não escreve em stdout
uv run notadory-memory-mcp </dev/null
```

Os testes cobrem persistência, deduplicação, revisão, escopo, paginação, léxico,
append de termos, cache de conversa, nível 5 e registro das ferramentas MCP.

## Limitações da V1

- A busca depende de aliases, conceitos, metadados e texto literal.
- Não há classificação semântica ou criação automática de conceitos.
- Níveis são rótulos manuais; nível 5 não faz parte da recuperação rotineira.
- O cache depende de uma `conversation_key` estável do harness.
- O diretório de dados deve ser protegido pelas permissões do ambiente onde o
  servidor é executado.
