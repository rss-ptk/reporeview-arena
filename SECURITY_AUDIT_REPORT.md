# RepoReview Arena: auditoria de risco de conteúdo não confiável

**Data:** 2026-11-01  
**Escopo:** fluxo local `init`/snapshot, fluxo `run`/provedor, ferramentas do modelo e apresentação/importação de relatórios.  
**Tipo:** auditoria focada, sem alterações no código.

## Conclusão executiva

Não encontrei um caminho pelo qual a Arena importe ou execute diretamente um arquivo do repositório auditado. O `init` enumera arquivos Git e copia conteúdo UTF-8 para o snapshot; o `run` lê trechos e os envia ao provedor escolhido. O modelo recebe somente `list_files`, `read_file` e `search_code`. Não há shell, execução de testes ou carregamento dinâmico do código auditado no fluxo da aplicação.

O `init` inicia o executável Git localmente. Em fixtures temporárias, os comandos usados não acionaram callbacks inofensivos configurados como `core.fsmonitor` local/global nem filtros smudge declarados por `.gitattributes`. Isso reduz a hipótese de execução indireta pelos comandos atuais do snapshot, mas não transforma o app em uma sandbox geral nem cobre uma configuração Git diferente em cada máquina.

## Caminho e evidências

- `snapshot_repo` executa apenas `git rev-parse --show-toplevel`, `git diff --name-only HEAD`, `git rev-parse HEAD` e `git ls-files -z`, com argumentos separados e timeout. Depois lê bytes e grava arquivos regulares no snapshot. Não chama checkout, fetch, install, test runner ou script do projeto.
- `RepositoryReader` monta uma lista de caminhos e oferece leitura/pesquisa; `_execute_tool` despacha somente os três nomes de ferramenta permitidos. Nomes desconhecidos retornam `ERROR: unknown tool`.
- O prompt avisa que o conteúdo do repositório é dado não confiável. Isso reduz confusão, mas não elimina prompt injection; as limitações do modelo não são fronteira de segurança.
- A tela monta textos de relatórios via `textContent`; a importação usa JSON e valida caminhos/linhas citados. Não encontrei renderização de conteúdo do relatório com `innerHTML`.
- O README documenta o clone manual antes de `init`. Filtros Git podem ser executados durante checkout, segundo a documentação do Git; esse risco pertence à etapa de clone/checkout e à configuração do operador, fora do snapshot da Arena ([gitattributes](https://git-scm.com/docs/gitattributes)). Git também permite `core.fsmonitor` apontar para um comando hook ([git-config](https://git-scm.com/docs/git-config)).

## Riscos residuais

| Prioridade | Impacto | Probabilidade | Risco e pré-condição | Evidência e efeito |
|---|---|---|---|---|
| **Médio** | Alto | Média, condicionada ao envio autorizado | Código, comentários ou fixtures podem conter segredos que a denylist por nome/extensão não reconhece. | O modelo pode solicitar trechos em várias chamadas; o teto de passos é 200. O `--confirm-send-code` e a revisão do snapshot ajudam, mas não detectam todos os segredos. Um segredo presente em texto comum pode chegar ao provedor. |
| **Baixo a médio** | Alto | Baixa, requer escrita local concorrente | Leitura fora do snapshot por corrida entre validação e abertura de arquivo. | `read_file` confia no conjunto de caminhos montado na inicialização e lê o caminho novamente sem revalidar symlink/raiz. `snapshot_repo` também separa checagem de symlink/resolução da abertura do arquivo. A condição exige escrita concorrente local; não é acionada pelo conteúdo do repositório sozinho. Se explorada durante `run`, conteúdo externo poderia ser enviado ao provedor. |
| **Médio** | Médio | Média | Perda de integridade da auditoria por prompt injection. | O prompt pede que instruções embutidas sejam ignoradas, mas o modelo pode ser influenciado e omitir ou fabricar achados. A lista fechada de ferramentas impede que isso vire execução de shell pelo agente; validação humana continua necessária. |
| **Baixo a médio** | Médio | Baixa a média, depende de volume de arquivos | Consumo de recursos com quantidade muito grande de arquivos pequenos. | Há limites por arquivo (180 KB) e por conteúdo total (16 MB), mas `git ls-files -z` e a lista de arquivos ignorados são materializados sem limite explícito de quantidade. Isso pode consumir memória/CPU antes do limite de bytes. |

Não classifiquei execução arbitrária de scripts do projeto como risco confirmado do caminho atual da Arena. Scripts presentes no snapshot continuam sendo conteúdo legível para o modelo, não tarefas executadas pela Arena.

## Validação controlada

- Fixture Git descartável com `malicious.py` e `malicious.sh` contendo ações que criariam um marcador: após `snapshot_repo`, o marcador não existia; os arquivos foram copiados como conteúdo.
- Callbacks inofensivos de `core.fsmonitor`, `diff.external` e filtro smudge foram configurados em `.git/config` e em um arquivo global Git isolado. Nenhum marcador foi criado ao executar `snapshot_repo`, inclusive no caso de checkout sujo usado para exercitar `git diff`.
- Provedor falso recebeu um arquivo com texto de prompt injection; após ler o arquivo, tentou chamar uma ferramenta `shell`. A Arena retornou `ERROR: unknown tool`; o marcador de execução permaneceu ausente. Nenhuma chamada externa foi feita.
- `python -m pytest tests/test_core.py tests/test_recovery.py -q`: **31 passaram**.
- Os probes de Git foram executados com Git for Windows **2.54.0.windows.1**.
- Os testes existentes de traversal passaram junto com a suíte. Não consegui executar a prova dinâmica de symlink: a criação de symlink foi recusada pelo Windows (`OSError`). A corrida descrita acima é uma conclusão estática, não uma reprodução confirmada.

## Nota sobre o Git Credential Manager observado

O snapshot Radiola contém [`update_and_test.sh`](workspace_arena/snapshot/update_and_test.sh), com `git fetch origin main` e, condicionalmente, `git pull`. A Arena não invoca esse script; ele é apenas um arquivo do snapshot. Durante a investigação anterior, foi observado um processo `git-remote-https` para um remoto `dev.azure.com` seguido de `git credential-manager get`, mas o processo pai já havia terminado. Portanto, não há evidência suficiente para atribuir aquela janela ao script ou ao processo da Arena. Executar o script manualmente ou por automação externa pode iniciar uma operação Git remota e solicitar credenciais.

## Recomendações priorizadas

1. Para reduzir a corrida local, abrir arquivos por descritor com semântica `no-follow`, revalidar o caminho/arquivo no momento da leitura e manter o snapshot em diretório não gravável por outros processos durante a auditoria.
2. Adicionar limite explícito de quantidade de arquivos e tamanho total da listagem do Git, além dos limites de bytes já existentes.
3. Antes do envio, adicionar detecção de segredos ao snapshot e apresentar uma lista revisável dos arquivos/trechos que podem chegar aos provedores; manter a confirmação explícita.
4. Preservar a lista fechada de ferramentas e a validação humana dos achados; nunca executar comandos, testes ou instruções sugeridas pelo conteúdo ou pelo modelo no processo da Arena.

## Limites

Esta foi uma auditoria focada no fluxo de conteúdo e execução, não um pentest geral nem uma revisão das políticas de retenção dos provedores. A prova dinâmica de symlink não foi possível neste host. O resultado dos callbacks Git aplica-se à versão do Git e ao ambiente isolado usados no teste.
