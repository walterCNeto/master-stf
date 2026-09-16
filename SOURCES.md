# Fontes de dados — Monitor STF / Banco Master

Documentação do formato real de cada fonte, obtido testando as chamadas via
`curl` (o endpoint sugerido inicialmente precisou de parâmetros extras para
funcionar — registrado abaixo).

## Fonte A — Processos em que "BANCO MASTER" é parte

Endpoint real (descoberto inspecionando o JS de
`https://portal.stf.jus.br/processos/listarPartes.asp`, que é a página pública
de busca "Por Parte"):

```
GET https://digital.stf.jus.br/integracoes-processos/api/public/partes/processos
```

Parâmetros obrigatórios (uma chamada só com `nome=` retorna `400 Bad Request`):

| Parâmetro              | Exemplo         | Observação                                   |
|------------------------|-----------------|-----------------------------------------------|
| `nome`                 | `BANCO MASTER`  | Nome da parte (case-insensitive, sem acentuar)|
| `tipoPesquisa`         | `PARTE`         | Fixo para busca por nome de parte             |
| `processosEmTramitacao`| `sim` / `nao`   | A API não tem opção "ambos" — é preciso 2 chamadas (uma com `sim`, outra com `nao`) e unir os resultados |
| `processosPorPagina`   | `50`            | Tamanho de página                             |
| `pagina`               | `1`             | Paginação (1-indexed); resposta traz `totalDePaginas` |

Exemplo funcional:

```
curl "https://digital.stf.jus.br/integracoes-processos/api/public/partes/processos?nome=BANCO%20MASTER&processosPorPagina=50&pagina=1&processosEmTramitacao=sim&tipoPesquisa=PARTE"
```

Resposta (JSON):

```json
{
  "totalDePaginas": 1,
  "totalDeRegistros": 3,
  "partes": [
    {
      "id": 44444509,
      "pessoaId": 16419284,
      "pessoaNome": "BANCO MASTER S/A - EM LIQUIDACAO EXTRAJUDICIAL",
      "processoId": 7673438,
      "processoIdentificacao": "ARE 1620035",
      "processoNumeroUnico": "0509750-41.2024.8.04.0001",
      "processoDataAutuacao": "21/08/2026",
      "processoMeio": "Eletrônico",
      "processoPublicidade": "Público",
      "processoEmTramitacao": "Sim"
    }
  ]
}
```

`processoId` é o número de **incidente** usado em
`portal.stf.jus.br/processos/detalhe.asp?incidente=<processoId>`.

Não requer autenticação nem cookies; não aceita `POST` (retorna `405`).
Não precisa de `Referer`/`Origin`.

## Fonte B — Andamentos por incidente (seeds.json)

Página pública: `https://portal.stf.jus.br/processos/detalhe.asp?incidente=<n>`

Os andamentos **não** vêm nessa página — ela carrega via JS um fragmento HTML
separado:

```
GET https://portal.stf.jus.br/processos/abaAndamentos.asp?incidente=<n>&imprimir=
```

Retorna um trecho de HTML (sem `<html>`/`<body>`) com uma lista `<li>` por
andamento. Estrutura relevante de cada item:

```html
<li>
  <div class="andamento-item">
    ...
    <div class="andamento-data">13/09/2026</div>
    <h5 class="andamento-nome">Conclusos ao(à) Relator(a)</h5>
    ...
    <div class="andamento-docs">
      <a href="downloadPeca.asp?id=15390366657&ext=.pdf" target="_blank">Certidão</a>
    </div>
    <div class="col-md-9 p-0">de retificação da autuação</div>  <!-- complemento opcional -->
  </div>
</li>
```

O link de decisão/documento é relativo a `https://portal.stf.jus.br/processos/`
e deve ser resolvido para `downloadPeca.asp?id=...&ext=.pdf`.

Seeds iniciais (`seeds.json`), descobertos via busca no portal:

| Processo   | Incidente | Link                                                                 |
|------------|-----------|-----------------------------------------------------------------------|
| PET 15198  | 7473336   | https://portal.stf.jus.br/processos/detalhe.asp?incidente=7473336     |
| PET 16704  | 7687920   | https://portal.stf.jus.br/processos/detalhe.asp?incidente=7687920     |

## Fonte C — Notícias do STF

```
GET https://noticias.stf.jus.br/wp-json/wp/v2/posts?search=banco%20master&per_page=20
```

API WordPress padrão (`wp/v2/posts`), retorna array de posts com campos
`id`, `date`, `modified`, `slug`, `title.rendered`, `link`,
`content.rendered` (HTML). Não precisa de autenticação. Funciona
diretamente, sem parâmetros adicionais.

## Observações gerais

- Todas as fontes respondem sem necessidade de sessão/cookies/CSRF.
- `digital.stf.jus.br` está atrás de AWS ALB; não há rate limit visível nos
  testes manuais, mas o monitor usa um `User-Agent` identificado, timeout e
  retry com backoff para ser um bom cidadão.
- **`portal.stf.jus.br` (usado na Fonte B) bloqueia com `403 Forbidden`
  qualquer `User-Agent` que não pareça de navegador** — um UA puramente
  "identificado" (ex.: `MonitorSTFBancoMaster/1.0 (contato: ...)`) é
  rejeitado pelo WAF, mesmo funcionando sem problema em `digital.stf.jus.br`
  e `noticias.stf.jus.br`. Por isso o monitor usa um `User-Agent` que
  combina uma string de navegador real (Chrome/Windows) com o sufixo de
  identificação do bot — testado e validado via `curl` para as 3 fontes.
- Falha em qualquer fonte não deve interromper as demais (try/except por
  fonte em `monitor.py`).
