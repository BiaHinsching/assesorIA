# Decisão técnica: transacionar para que apenas o que passe pelo juiz fique no banco

Ao analisar o projeto juntamente da minha dupla optamos por seguir uma das 4 sugestões do enunciado: transacionar. Antes de realizarmos essa implementação, cada tool fazia commit() e fechava a conexão com o banco postgres, agora a escrita no banco fica numa transação por turno, aguardando o veredito do juiz, caso seja recusado rodamos rollback, e se aprovado commit. Por padrão, tudo é desfeito, a não ser com aproação do juiz. A transação chega às tools pelo config, de forma similar ao que fazíamos com o historico. 

Escolhemos transacionar por considerar a maneira mais correta e técnica de resolver o problema atacando a causa, não sintoma. Tínhamos o problema de que salvávamos no banco com commit diretamente na tool, o que resolvemos foi exatemente essa questão, Compensar, por exemplo, apenas atacaria o sintoma, que é o registro de dados reprovados pelo juiz.

Transacionar também é vantajoso já que a garantia de confiablidade no dado vem do banco e não depende apenas do nosso código. É uma solução que cobre as 3 tools, de forma pouco invasiva, com uma refatoração localizada.

Transacionando, também não corremos o risco de alguém enxergar o banco num estado temporário, como aconteceria caso escolhessemos Compensar (antes de rodarmos o DELETE, alguém poderia rodar uma leiturar e ver um estado incorreto no banco).

Porém também mesclamos um pouco as soluções: no caso do add_google_event, ao invés de Transacionar, já que o Google Calendar não é um banco de dados e um rollback não existe lá, nós decidimos Adiar, e portanto algo só é salvo no calendar após aprovação do juiz.

## Opções descartadas

* Interceptar - poderíamos ter um validador antes das tools (um agente ou jev, talvez), mas isso traz alguns problemas, além do custo desse validador e tempo que ele adicionaria numa resposta. Por exemplo, a ação pode ser aprovada e a resposta depois ser recusada pelo Juiz, e a escrita ficaria. Acho uma solução pouco refinada e não resolve completamente o problema.

* Adiar (usada para o Calendar apenas) - nesse cenário a tool só prepararia e é gravado pelo sistema após veredito do juiz. Até considero essa uma boa opção, mas extremamente invasiva para um caso de uso com Postgres, ja que mudaríamos o retorno das tools, prompts, regra do juiz etc. Com a impossibilidade de Transacionar o Google Calendar, foi a opção escolhida para esse caso de uso.

* Compensar - resume-se em apagar os registros recusados ou retorná-los ao estado antigo em caso de UPDATE. Essa me parece a pior opção e a mais suscetivel a falhas (algumas já foram listadas anteriormente na parte de decisão técnica), um exemplo de falha: alguém roda SELECT no banco entre o momento em que gravamos e o veredito do juiz com a compensação, e vê um estado incorreto do banco. Além disso, como já dito, é uma solução que ataca um sintoma e não a causa, sendo pouco refinada.


## Evidências

Para testar o funcionamento do sistema rodei uma bateria de testes:

Turno 1
Prompt: "Gastei 37,13 reais no mercado hoje, anota aí QA29a-1"
Resposta: "Lançamento de R$ 37,13 em “comida” registrado com sucesso."
Resultado: salvou com sucesso no banco de dados.

Turno 2
Prompt: "Comprei um fone por 50 dólares hoje, registra QA29a-2"
Resposta: "Não tenho evidência para afirmar isso."
Resultado: Juiz barrou a resposta, porém dessa vez NENHUM dado foi salvo no banco de dados.

Turno 3
Prompt: "Altera o gasto QA29a-1 para 41,00 reais"
Resposta: "- Atualizei o valor do gasto QA29a-1 para R$ 41,00."
Resultado: Valor alterado corretamente no banco de dados.

Turno 4
Prompt: "Tenho dentista amanhã às 15h, marca aí QA29a-4"
Resposta: "- Registrei “Dentista QA29a-4” em 30/10/2026 das 15:00 às 16:00. Observação: o compromisso foi salvo na agenda, mas não consegui sincronizá-lo com o Google Calendar agora."
Resultado: salvo com sucesso no banco de dados, assimo como esperado. Não foi sincronizado visto que meu google não estava autenticado, com objetivo de não sujar minha agenda durante essa fase de testes;

Turno 5
Prompt: "Quanto eu gastei com QA29a-1?"
Resposta: "Você gastou **R$ 41,00** no registro QA29a-1."
Resultado: Consulta realizada corretamente no Postgres.

Turno 6
Prompt: "Registra um gasto de 20 reais em QA29a-6 e me diz o saldo total"
Resposta: "- Lancei R$ 20,00 em “outros” referente a QA29a-6. Seu saldo total atual é de **-R$ 10,00**."
Resultado: escreveu e consutou corretamente o banco de dados, calculando o saldo correto dado minhas transações.

TESTANDO COM UMA CHAVE INVÁLIDA DA TYPESAFE

Para forçar a recusa do juiz de forma garantida, subi uma segunda instância da API com a chave da Typesafe inválida. O juiz recebe erro 401 e recusa tudo ([juiz] erro http 401), mas as tools ainda executam a escrita (pendente na transação) antes do rollback.

Turno 7
Prompt: "Altera o gasto QA29a-1 para 55,00 reais"
Antes: amount do QA29a-1 = 41,00
Resposta: "Não tenho evidência para afirmar isso."
Log: [juiz] erro http 401
Resultado: o juiz recusou e o valor continuou 41,00 no banco (nenhuma alteração permaneceu).

Turno 8
Prompt: "Tenho academia sexta às 7h, marca aí QA29a-8"
Antes: 0 eventos com QA29a-8
Resposta: "Não tenho evidência para afirmar isso."
Log: [juiz] erro http 401
Resultado: o juiz recusou e continuam 0 eventos com QA29a-8 no banco. Como indício de que o INSERT chegou a rodar e foi desfeito, o contador (sequence) da tabela events está em 4 enquanto o maior id existente é 3: um id foi consumido por um INSERT que sofreu rollback.


## Efeito nas métricas de SRE

**Como medimos.** Subimos duas instâncias da API, uma com o código original (commit c06e81f) e outra com o código novo, cada uma ligada a um banco Postgres descartável idêntico. Mandamos os mesmos pedidos para as duas, alternando entre elas, em duas execuções de 10 pedidos cada (5 pedidos por rodada: gasto, alteração do gasto, evento na agenda, consulta e gasto mais saldo), o que dá **20 turnos por versão**. Os números vêm do `/monitor` de cada instância, juntando as duas execuções. Com 20 amostras, o p95 é a 19ª em ordem crescente, isto é, a segunda pior, então um único turno lento pesa muito. Por isso mostramos também a mediana.

| | Original | Nova (transacionar) |
|---|---|---|
| Turnos | 20 | 20 |
| Latência média | 17,2 s | 21,6 s |
| Mediana | 11,0 s | 11,8 s |
| p95 (19ª de 20) | 76,7 s | 109,4 s |
| Pior turno | 82,5 s | 122,0 s |
| Turnos acima de 8 s | 13 de 20 | 14 de 20 |
| Taxa de erro | 20% (4 de 20) | 20% (4 de 20) |
| Tokens (soma dos 20) | 223.391 | 229.428 |
| Custo (soma dos 20) | US$ 0,00604 | US$ 0,00618 |
| Tempo com transação aberta, turnos de escrita | 0 (cada tool dava commit na hora) | mediana 7,3 s; de 4,4 s até 115,8 s (12 turnos) |

**Transacionar (a escolhida):**
- *Latência:* a mediana ficou praticamente igual (11,0 s e 11,8 s), e o custo e os tokens também. Em ambas as versões, mais da metade dos turnos passou dos 8 s do SLO, então a lentidão já existia no código original. O p95 maior da versão nova (109,4 s contra 76,7 s) vem de dois turnos de agenda muito lentos (109 s e 122 s). A original também teve turnos de agenda de 77 s e 83 s, então a lentidão da agenda vem de antes da solução e não sabemos a causa. O commit em si não é onde está o custo.
- *Custo real da solução:* a transação fica aberta enquanto o turno continua. Nos turnos de escrita normais medimos de 4,4 s a 16,1 s (mediana 7,3 s), entre 33% e 75% da duração do turno. Nos dois turnos lentos de agenda a transação ficou aberta por **97 s e 116 s**, quase o turno inteiro. Ela abre na primeira tool e só termina depois do orquestrador, do guardrail de saída e do Juiz, então qualquer lentidão no turno vira tempo de conexão ocupada. Na prática ocupa uma conexão do banco e, num UPDATE, segura o lock da linha. É o principal preço da escolha, e quanto mais lento for o turno, pior.
- *Erro:* as duas versões ficaram com 20% (4 erros em 20), acima do SLO de 5%, mas **todos os 8 erros vieram do roteador** (o modelo tenta chamar uma tool que não está na lista dele e o Groq responde 400), não da solução. Pelo código, uma falha no commit depois da aprovação vira exceção e conta como `erro`, e um turno recusado pelo Juiz termina em frase segura, sem exceção, e conta como `ok`.
- *Status `bloqueado`:* pelo código, `bloqueado` nunca aparece, porque o monitor procura `guardrail_entrada:` e o nó emite `guardrail_entrada`. O contador de bloqueios ficou em 0 nas duas versões. Isso foi concluído lendo o código, não testado com um pedido bloqueado.
- *Tokens e custo:* a solução não faz nenhuma chamada de modelo a mais, e a diferença de 3% nos tokens está dentro do ruído.
- *Conexão com o banco:* passa a ser uma por turno, aberta só na primeira vez que uma tool usa o banco, em vez de uma por tool.

**Interceptar (uma das descartadas), mecanismo, não medido:**
- Se o validador fosse o JEV, acrescentaria uma chamada por escrita, com latência e custo próprios, em cima de turnos que já passam do SLO.
- Essa chamada não apareceria no coletor do monitor, que só mede chat models do LangChain.

**Limites desta medição.** Os erros do roteador desorganizaram o pareamento dos pedidos: quando um turno falha, o pedido seguinte da mesma conversa acabou respondendo o pedido que tinha falhado, então nem todo turno de uma versão tem um equivalente exato na outra. O banco de teste tem um schema mínimo, sem as restrições reais. Uma amostra de 20 ainda é pequena para um p95 estável.

**Comprovação do problema original.** Nessas execuções, no código original o Juiz recusou a resposta de **2 pedidos de evento** ("Não tenho evidência para afirmar isso.") e mesmo assim o evento ficou gravado no banco nas duas vezes. É o Juiz Tardio acontecendo. Na versão nova nenhum turno foi recusado nessas execuções; as recusas sem efeito no banco estão documentadas nos turnos 2, 7 e 8 acima.

## Outras decisões
* Adiar escrita no Calendar;
* deixar salvar_mensagem fora de escopo, já que o comportamento atual já é coerente. A resposta só é gravada depois da decisão do juiz então fica igual ao que o usuário viu. A pergunta do usuário é gravada antes do juiz, no guardrail de entrada, mas isso também não é problema, já que ela não é uma escrita de negócio e o Mongo não faz parte da transação do Postgres;
* se o juiz não responde (sem chave, erro, timeout de 30s ou exceção), tratamos como recusa. O usuário vê a frase segura e nada é salvo no banco, já que o padrão é desfazer. Para o usuário, uma falha do juiz é igual a uma recusa de verdade;
* se o processo cair antes do commit, o próprio Postgres desfaz a transação aberta e nada fica salvo. Se cair depois do commit e antes do Google, o evento fica no Postgres mas não vai pro Calendar, e como não temos nova tentativa esse espelho se perde. Se o próprio commit falhar depois da aprovação, vira exceção e nada é salvo;
* repetir o mesmo pedido grava duas linhas. A API não tem um identificador de pedido (e não podíamos alterá-la), então não dá para distinguir uma repetição de um segundo pedido igual feito de propósito. O mesmo vale se o modelo chamar a mesma tool duas vezes no mesmo turno. É uma limitação conhecida, que seria resolvida com uma chave de idempotência, mas não implementamos.

## Lista de arquivos criados e modificados:

**Criados:** `tests/helpers_juiz.py`, `tests/test_unidade_trabalho.py`, `tests/test_escritas_juiz.py`, `tests/test_juiz_tardio_postgres.py`, `decisao_tecnica.md`.

**Modificados:**
- `app/db.py`: transação do turno com savepoints.
- `app/tools/financeiro.py`: tools usam a transação do turno e não confirmam sozinhas.
- `app/tools/agenda.py`: idem.
- `app/tools/calendario_google.py`: o Google passa a ser chamado só depois do veredito.
- `app/juiz.py`: o veredito vira dado (`juiz_aprovado`).
- `app/graph.py`: o ciclo do turno decide commit ou rollback depois do Juiz.
- `app/prompts.py`: o prompt da agenda diz que a sincronização com o Google fica pendente.

(Uso de IA neste documento: a seção de arquivos criados e modificados foi gerada por IA, apenas com o fim de mostrar onde as decisões já explicadas acima se concentram no código. A seção de métricas de SRE foi montada com ajuda de agentes de IA, que também executaram os testes: subiram as instâncias da API (código original e código novo), rodaram os pedidos de teste, coletaram os dados do /monitor e do banco e ajudaram na análise dos resultados.)