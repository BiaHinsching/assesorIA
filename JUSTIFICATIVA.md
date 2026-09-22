# JUSTIFICATIVA — Perfil financeiro do usuário

## 1. Quais arquivos você criou ou modificou?

**Criados**
- `app/perfil.py` — persistência do perfil (Mongo + Qdrant) e leitura.
- `app/routes/perfil.py` — rota `POST /perfil`.
- `app/tools/perfil.py` — as duas tools do especialista financeiro.
- `tests/test_perfil.py` — testes offline da persistência e das tools.

**Modificados**
- `app/schemas.py` — `PerfilRequest` (contrato + validação) e `PerfilResponse` (eco).
- `app/routes/__init__` (implícito) / `app/main.py` — `app.include_router(perfil.router)`.
- `app/graph.py` — `financeiro_app` ganhou `TOOLS_PERFIL` (só ele).
- `app/prompts.py` — seção `### PERFIL DO USUÁRIO` no `FINANCEIRO_PROMPT`.
- `app/guardrail.py` — o classificador só bloqueia pedido de ativo ESPECÍFICO
  nomeado; "vale a pena cripto pra mim?" volta a ser APROVADO para o
  especialista responder ancorado no perfil.
- `frontend/perfil.html`, `perfil.css`, `perfil.js` — movidos para a raiz de
  `frontend/` (o enunciado pede `localhost:8000/perfil.html`, e o mount serve
  `frontend/` em `/`). O frontend do chat não foi tocado.

## 2. Por onde o perfil entra, e onde cada parte dele é gravada?

Entra pela tela Perfil → `POST /perfil` → `PerfilRequest` (valida) →
`app.perfil.salvar_perfil()`. Um único ponto de escrita.
- **Estruturado** (`renda_mensal`, `objetivo`, `tolerancia_risco`) → MongoDB,
  collection nova `perfis`, `_id = user_id`.
- **Preferências** (texto livre) → Qdrant, collection nova
  `perfil_preferencias`, uma frase por ponto, `payload = {user_id, texto}`.

## 3. Como as preferências são guardadas e consultadas, e por que não é busca por palavra?

O texto é quebrado em frases; cada frase vira um vetor (embedding
`gemini-embedding-2-preview`, 768d) num ponto do Qdrant. A consulta gera o
embedding do assunto perguntado e o Qdrant devolve as frases mais próximas por
similaridade de cosseno, filtrando por `user_id`. Não é busca por palavra
porque a restrição precisa ser encontrada mesmo sem o termo aparecer: o perfil
diz "não quero investimento agressivo" e a pergunta é sobre "cripto" — só
comparando significado (vetores) isso casa; um `$regex`/`LIKE` daria zero.

## 4. Você criou uma tool ou duas? Por quê?

Duas. Os dois tipos de dado têm padrão de acesso diferente (o próprio enunciado
os separa). `consultar_perfil` é lookup determinístico, sem argumento — o
modelo só quer os fatos (renda/objetivo/risco). `buscar_preferencias` precisa
que o modelo passe o **assunto** (`consulta`) para a busca vetorial. Uma tool
só forçaria o modelo a inventar uma query mesmo quando quer apenas a renda, e
misturaria um lookup exato com uma busca semântica no mesmo retorno.

## 5. O que garante que o perfil de um usuário não apareceria para outro?

Três camadas: (a) o `user_id` vem do **contexto da requisição**
(`config["configurable"]["user_id"]`), não é argumento que o modelo preenche —
o modelo não consegue pedir o perfil de outra pessoa; (b) no Mongo a consulta é
`{"_id": user_id}`; (c) no Qdrant toda consulta passa
`Filter(must=[FieldCondition(key="user_id", match=user_id)])`, e todo ponto
carrega `payload.user_id`. Sem `user_id` no contexto, a tool responde "não foi
possível identificar o usuário" e não consulta nada.

## 6. Sua tool consulta o banco diretamente ou faz HTTP na própria API? Por quê?

Direto no banco (via `app.perfil`). A rota HTTP `/perfil` é só de **escrita**,
para a tela. A tool roda dentro do mesmo processo do agente; chamar a própria
API por HTTP seria um salto de rede desnecessário, com serialização, timeout e
um servidor tendo que responder a si mesmo. É o mesmo padrão das tools de
finanças (Postgres direto) e de memória.

## 7. Por que não existe um agente "perfil"?

Perfil não é um domínio de conversa — ninguém "fala com o perfil". É dado de
**apoio** de quem já aconselha dinheiro. Um agente novo significaria uma rota
nova no roteador, um prompt novo e uma saída JSON nova, tudo para entregar
três campos e um texto que o especialista financeiro consome. As tools no
`financeiro_app` resolvem sem inflar o grafo. Roteador, agenda e FAQ não
recebem essas tools.

## 8. Por que o chat não altera o cadastro?

Não existe tool de escrita de perfil — o agente só tem `consultar_perfil` e
`buscar_preferencias`, ambas de leitura. E o `FINANCEIRO_PROMPT` instrui: se o
usuário pedir para mudar renda/objetivo/risco/preferências no chat, explicar
que a alteração é feita só pela tela Perfil. A escrita é responsabilidade
exclusiva de `POST /perfil`, que só a tela chama.
