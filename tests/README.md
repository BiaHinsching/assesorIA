# Testes

Suíte **100% offline** — nenhum teste bate em Gemini, Groq, Postgres, MongoDB ou
Qdrant. Todos os clientes externos são mockados. Roda em ~4s.

## Rodar

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/conftest.py` seta variáveis de ambiente falsas antes de qualquer
`import app.*`, então não precisa de `.env` para rodar os testes.

## O que cada arquivo cobre

| Arquivo | Cobre |
|---|---|
| `test_llm_fallback.py` | **Regressão do bug do fallback** — `bind_tools` propaga para o modelo primário E o de fallback; fallback do especialista não é `gpt-oss`; roteador é instância própria |
| `test_memory.py` | `iniciar_sessao` / `salvar_mensagem` / `encerrar_sessao` (mongomock); indexação do resumo no Qdrant; `recuperar_historico` semântico vs. fallback no Mongo; falha no Qdrant não derruba o encerramento |
| `test_guardrail.py` | Anonimização de PII (CPF, e-mail, telefone...), desanonimização, padrões de injeção, classificação por categoria (LLM mockado) |
| `test_tools.py` | `buscar_historico` (parsing do `config`, formatação de data datetime/ISO); `faq_retriever` (shape da query no Qdrant) |
| `test_graph.py` | Funções de decisão de rota; parsing do `ROUTE=` no `no_roteador`; repasse do `config` |
| `test_schemas_vectorstore.py` | Default de `user_id` no `ChatRequest`; `gerar_embedding` pede 768 dimensões; nomes das collections |

## Quando um teste real ainda é necessário

Estes só um smoke test com serviços de verdade pega:
- Latência / cota real das APIs
- Schema real do Postgres batendo com as queries de `tools/financeiro.py`
- Um modelo novo do Groq quebrando em tool-calling ou saída JSON
