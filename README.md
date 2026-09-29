# Notadory Memory MCP

Servidor MCP por **stdio** para memórias versionadas em arquivos JSON. Requer
Python 3.10 ou superior.

## Instalação

```sh
uv sync --extra dev
```

Os dados ficam em `memory-data/` relativo ao diretório de execução por padrão.
Defina `MEMORY_DATA_DIR=/caminho/dos/dados` para usar outro diretório. O
servidor cria `lexicon/concepts.json`, os diretórios de memórias e o cache de
conversas automaticamente.

## Execução

```sh
uv run notadory-memory-mcp
# ou
uv run python -m notadory_memory.server
```

Exemplo curto de configuração em um host MCP:

```json
{
  "mcpServers": {
    "notadory-memory": {
      "command": "uv",
      "args": ["run", "--directory", "/caminho/notadory", "notadory-memory-mcp"],
      "env": {"MEMORY_DATA_DIR": "/caminho/notadory/memory-data"}
    }
  }
}
```

O host deve iniciar esse comando como processo stdio (não redirecione logs
para stdout). As sete ferramentas são `create_concept`, `create_memory`,
`list_memories`, `get_memory`, `load_memory`, `retrieve_context` e
`reindex_archive`.

Escopos são objetos: `{"type":"global"}` ou
`{"type":"project","project_id":"meu-projeto"}`. Conceitos ficam em
`lexicon/concepts.json`; overrides opcionais ficam em
`lexicon/project-overrides/<project>.json`.

`create_concept(concept_id, label, aliases, action_terms, object_terms, scope)`
cria um conceito durável. Sem `scope`, ou com `{"type":"global"}`, ele é
global; com `{"type":"project","project_id":"meu-projeto"}`, fica no
override desse projeto. O host/agente deve criar conceitos apenas quando
precisar representar vocabulário durável. Não crie um alias para uma
preferência ou tecnologia específica de um projeto quando isso deve ser uma
memória.

## Instrução para o host

Antes de implementar uma alteração que dependa de convenções, arquitetura ou
decisões do projeto, consulte `retrieve_context` para o escopo atual e confira
o código antes de editá-lo. Não carregue nível 5 na rotina nem salve conversas
ou tarefas transitórias. Se uma memória divergir do código, sinalize a
divergência em vez de tratá-la como verdade.

## Desenvolvimento

```sh
uv run pytest
```
