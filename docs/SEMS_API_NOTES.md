# SEMS Portal / SEMS+ API – notas de pesquisa

> Documento interno de pesquisa (não faz parte da documentação do binding). Veio de
> `bundles/org.openhab.binding.semsportal` do branch `feature/semsportal` do fork do openhab-addons, depois que a
> migração do binding foi mesclada no openHAB oficial (PR #22184, 2026-11-06).
> Última atualização: **2026-11-06**. Objetivo: não perder o que foi descoberto na migração de setembro/2026
> e ter um roteiro para reavaliar quando a GoodWe desligar os endpoints legados restantes.

## A. Estado atual (2026-11-06): o binding usa a API do SEMS+

Em 2026-10-28, por volta das 19h, os dados em tempo real da API legada congelaram (`GetInverterAllPoint`
passou a dizer `status -1`, 0 W e `last_refresh_time` parado), enquanto o SEMS+ mostrava a usina normal. O binding
mostrava "Station not operational". Por isso ele foi migrado para o SEMS+ (a API legada não é mais usada).
A migração do gw2pvo está em `SEMS_PLUS_MIGRATION.md` (nesta mesma pasta); o protocolo é o mesmo.

**Protocolo** (login em `semsplus.goodwe.com`, o resto no gateway regional que o login devolve em `data.api`):

- Login: `POST https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login`, corpo
  `{account, pwd: base64(md5hex(senha)), agreement: 1, isChinese: false, isLocal: false}`. Sem cabeçalhos de
  navegador (`User-Agent`, `Origin`, `Referer`) o login é recusado com `100004`.
- Toda chamada leva `token` (JSON de `data` do login) e `X-Signature` =
  `base64(sha256hex("<ms>@<uid>@<token>") + "@" + <ms>)`; o login assina com token vazio.
- Sucesso é `code "00000"`. Sessão vencida ou substituída: `C0602` (ou `100002`). Senha errada: `100005`.
  Estação inexistente ou sem acesso: `100025` (o `all-status` responde OK e vazio).
- **Uma sessão por tipo de cliente da conta**: um login novo derruba o token anterior do mesmo tipo. O tipo é o
  `client` do token do login. Aceitos: `semsPlusWeb`, `web`, `ios`, `android` (qualquer outro valor é recusado).
  O gw2pvo do servidor usa `web`; o binding usa `semsPlusWeb` por padrão (parâmetro `clientType` da bridge).
  Os logins da API legada e do SEMS+ não se afetam.

**Canais** (nomes e tipos inalterados):

| Canal | Fonte |
|---|---|
| `currentOutput` (W) | `GET /sems-plant/api/stations/flow?stationId=` → `pAc` (kW × 1000) |
| `lastUpdate` | `flow` → `refreshTime` (ISO, hora da usina; lida no fuso do sistema) |
| `todayTotal`, `monthTotal`, `overallTotal` (kWh) | `GET /sems-plant/api/equipments/<sn>/telecounting?deviceType=INVERTER&pwId=` → `proPvStatsToday`, `proPvStatsMonth`, `proPvStatsTotal`, somados dos inversores |
| `todayIncome` | `POST /sems-plant/api/stations/production`, `dimension: day` → `profitProStats` |
| `totalIncome` | idem, `dimension: year`, de `2000-01-01` até hoje (o portal soma qualquer período) |
| operacional | `GET /sems-plant/api/stations/device/all-status?stationId=` → status de algum inversor em 1 ou 5 |
| descoberta | `POST /sems-plant/api/portal/stations/page` `{current: 1, size: 100}` → `id`, `name`, `pvInstallP` |

Cuidados: o `production` com só `profitProStats` dá `B0001 system_execution_error`; é preciso pedir também
`proSystemTotalStats`. As receitas são opcionais (se falharem, os canais ficam `NULL`). Por atualização são
`3 + 1 por inversor` chamadas, mais as duas de receita.

**Situação no openHAB oficial** (2026-11-06): a migração foi mesclada no `main` (5.3.0-SNAPSHOT) pelo PR #22184
(commit `77b3e74ee0`), com o `updateStatus(ThingStatusDetail, ...)` do PR #21904. A linha `5.2.x` oficial **não**
recebeu a mudança e continua falando com o `www.semsportal.com`, que está fora do ar; o backport ficou parado de
propósito (o branch `semsportal-sems-plus-5.2` do fork tem a versão para a 5.2.x, com o `pom.xml` reformatado
porque o da `5.2.x` não passa no `spotless:check`). O issue #21786 (NPE com os endpoints mortos) segue aberto.

## 0. Incidente 2026-10-26: `www.semsportal.com` responde 502

- `www.semsportal.com` e `us.semsportal.com` respondem `502 Bad Gateway` (nginx) a qualquer requisição.
- `eu.`, `au.` e `hk.semsportal.com` continuam aceitando o login legado (`v2/Common/CrossLogin`), e qualquer
  um deles devolve no campo `api` a API regional da conta. Para esta conta veio
  `https://hk.semsportal.com/api/`; em setembro vinha `https://us.semsportal.com/api/`.
- As chamadas de dados **têm que ir para o host do campo `api`**. O token emitido pelo `eu` é recusado pelos
  endpoints de dados do próprio `eu`, com `code: 100002` (sessão expirada). O binding tratava isso como sessão
  inválida e entrava no ciclo de novo login. Testado com `GetPlantDetailByPowerstationId`,
  `GetInverterAllPoint` e `QueryPowerStationByHistory`: todos `code: 0` no `hk` e `100002` no `eu`.
- Correção no `PortalHandler`:
  - o login tenta `www`, `eu`, `au` e `hk`, nessa ordem, e passa para o próximo host quando não há resposta
    válida (erro HTTP, rede ou JSON);
  - um login recusado (ex.: `100005`) para na hora, porque todos os hosts recusam igual. A resposta recusada
    **também traz `api`**, então o teste de sucesso tem que ser `code == 0`;
  - o `api` do login fica em `apiBaseUrl`, e status, inversores e lista de estações usam esse host;
  - a descoberta (`getAllStations`) faz login antes, se ainda não souber o `apiBaseUrl`.
- O gw2pvo (mesma conta) teve a mesma correção e foi validado em produção no mesmo dia.
- O issue do openhab-addons sobre o binding (#21786, NPE com `GetMonitorDetailByPowerstationId` vazio) continua
  aberto e sem resposta. O binding oficial ainda tem os dois problemas: os endpoints mortos e o `www` fixo.

## 1. Resumo

- Em **2026-09-23** a GoodWe ativou o "novo ambiente SEMS+" e o endpoint legado
  `v2/PowerStation/GetMonitorDetailByPowerstationId` passou a responder `code: 0` com `data: {}`.
  Isso quebrou este binding e praticamente todas as integrações da comunidade.
- O binding foi migrado (commit `b60d359569`) para **outros endpoints legados que continuam funcionando**
  (`v3/PowerStation/GetPlantDetailByPowerstationId` + `v3/PowerStation/GetInverterAllPoint`), com diff mínimo.
  O login legado não mudou.
- O **SEMS+ tem API REST** (não documentada, usada pelo SPA web). Não é necessário scraping.
  A principal integração da comunidade (Home Assistant, TimSoethout) migrou 100% para ela em 2026-09-25.
- Decisão atual: **manter os endpoints legados** enquanto funcionarem. A API web do SEMS+ é o plano B,
  mas está instável (muitas releases/erros novos por dia na comunidade).

## 2. API legada (www.semsportal.com) – estado em 2026-09-25

Base: `https://www.semsportal.com/api/` (o binding usa `BASE_URL + "api/..."`).

### 2.1 Login (inalterado)

```
POST https://www.semsportal.com/api/v2/Common/CrossLogin
Header  Token: {"version":"v2.1.0","client":"ios","language":"en"}
Body    {"account":"<email>","pwd":"<senha em texto>"}
```

- Resposta: `data` = `{uid, timestamp, token, client, version, language, uuid, neutral}` → serializado como JSON
  no header `Token` das chamadas seguintes. Também vem `api` (ex.: `https://us.semsportal.com/api/`).
- Testado e **não necessário** (tudo funciona sem):
  - token `v3.1` (o `v2.1.0` antigo funciona);
  - `User-Agent` de app iOS (o default do Jetty funciona);
  - usar a URL regional do campo `api` (o `www` atende todas as chamadas) — **deixou de valer em 2026-10-26**,
    ver seção 0;
  - corpo form-encoded (JSON funciona igual);
  - campos `uuid`/`neutral` no token.
- O campo `api` sempre existiu (o fixture de 2021 já tinha `https://eu.semsportal.com/api/`).
  Quando o `www` parou de rotear (2026-10-26), usar a URL regional foi de fato a correção.
- Nota: a integração do HA usa `v3/Common/CrossLogin` com token `"version":"3.1.1"`; não foi necessário aqui.

### 2.2 Endpoints mortos (respondem OK, mas vazios)

| Endpoint | Sintoma |
|---|---|
| `v2/PowerStation/GetMonitorDetailByPowerstationId` | `code 0`, `data: {}` |
| `v2/PowerStationMonitor/GetPowerStationPacByDayForApp` | lista vazia |
| `PowerStationMonitor/QueryPowerStationMonitorForApp` | `data: {messageKey, list: []}` (antes era array) |
| `v2/PowerStationMonitor/GetPowerStationPowerAndIncomeByDay` | **parcial**: só o dia atual tem valor; dias passados vêm `p: 0` |

### 2.3 Endpoints que funcionam (usados pelo binding)

**Status da planta** – `v3/PowerStation/GetPlantDetailByPowerstationId`, body `{"powerStationId":"<uuid>"}`

- `data.kpi`: `pac` (W), `power` (kWh hoje), `month_generation`, `total_power`, `day_income`, `total_income`,
  `yield_rate`, `currency`. É o mesmo bloco `kpi` do endpoint morto.
- `data.info`: `powerstation_id`, `stationname`, `address`, `capacity`, `status`, `time` (hora da consulta,
  **não** da última leitura), `date_format_ym`, `local_date`. **Não tem mais `date_format`.**
- Estação inexistente → `code 0` com `data: null` (antes era `code: "innerexception"`).

**Inversores (tempo real)** – `v3/PowerStation/GetInverterAllPoint`, body `{"powerStationId":"<uuid>"}`

- O minúsculo funciona; o gw2pvo usa `PowerStationId` com P maiúsculo.
- `data.inverterPoints[]`: `sn`, `name`, `out_pac`, `in_pac`, `eday`, `emonth`, `etotal`, `status`
  (-1 offline, 0 waiting, 1 normal, 2 fault), `hTotal`, `last_refresh_time`, `local_date`.
- `dict.left/right[]`: `{key, value, unit}`, com por exemplo `acVacVol`, `acCurrent`, `acFrequency`, `innerTemp`,
  `DeviceParameter_capacity`, `dmDeviceType` (modelo) e `dcVandC1..3` (string `"V/A"`, ex. `"143.5/0.0"`).
- `points[]`: metadados das colunas disponíveis para `GetInverterDataByColumn` (Vpv1, Ipv1, Vac1, Pac, ETotal…).
- Estação inexistente → `code 0`, `data: null`.

**Lista de estações** – `v2/HistoryData/QueryPowerStationByHistory`

- Aceita o body antigo do `StationListRequest` (ignora os campos), `{"page":1,"pagesize":50,"key":""}` ou `{}`.
- `data.record`, `data.list[]`: `id`, `pw_name`, `pw_address`, `status`, `inverters[]` `{sn, name, status}`.
- O binding mapeia `id`/`pw_name` com `@SerializedName(alternate=...)` no DTO `Station`.

**Outros que funcionam (não usados pelo binding)**

- `v2/PowerStationMonitor/GetInverterDataByColumn` `{"id":"<sn>","date":"YYYY-MM-DD","column":"Pac"}`
  → `data.column1[] {sn, date, column}` a cada 5 min (também Vpv1, Ipv1 etc.). Substitui o `GetPowerStationPacByDayForApp`.

### 2.4 Códigos de resposta

| code | Significado |
|---|---|
| `0` / `"0"` | OK (pode vir número ou string) |
| `100001` | sem sessão ("No access, please log in") |
| `100002` | sessão expirada |
| `100005` | login inválido |
| `innerexception` | erro interno (antes: estação inválida) |

### 2.5 Particularidades importantes

- **`last_refresh_time` do inversor vem sempre em `MM/dd/yyyy HH:mm:ss`**, mesmo em conta BR.
  - Evidência: conta BR retornou `09/24/2026 18:31:46`, enquanto `laConnected` veio em `17/05/2021`.
  - Por isso o `success_status_br.json` foi removido: o campo antigo `d.last_refresh_time`, que era localizado
    conforme `info.date_format`, não existe mais.
  - Risco: se a GoodWe localizar esse campo, o parse quebra (não há fallback).
- **`emonth` do inversor = mês SEM o dia de hoje**; `kpi.month_generation = emonth + eday`.
  - Medido: 549.9 + 29.4 = 579.3 (24/09) e 579.3 + 3.4 = 582.7 (25/09).
  - O fixture de 2021 mostra o mesmo (17.2 + 0.5 = 17.7), ou seja, o canal `monthTotal` sempre ficou atrasado
    em um dia.
  - Correção possível (mudança de comportamento, não feita): usar `KeyPerformanceIndicators.getMonthPower()`.
- `currentOutput` vem do `kpi.pac` (planta toda), mas dia/mês/total/status vêm só do **primeiro** inversor
  (`stations.get(0)`). Com 2+ inversores fica inconsistente, e já era assim antes da migração.
  - Alternativa: `kpi.power` / `kpi.total_power` (planta).
- O binding faz 2 chamadas por atualização (planta + inversores). Se a sessão expirar entre elas, a segunda lança
  `CommunicationException` e o próximo ciclo refaz o login.

### 2.6 Mapeamento canal → campo (após a migração)

| Canal | Origem |
|---|---|
| `currentOutput` | `GetPlantDetail.kpi.pac` |
| `todayTotal` | `GetInverterAllPoint.inverterPoints[0].eday` |
| `monthTotal` | `inverterPoints[0].emonth` (sem hoje, ver 2.5) |
| `overallTotal` | `inverterPoints[0].etotal` |
| `todayIncome` / `totalIncome` | `kpi.day_income` / `kpi.total_income` |
| `lastUpdate` | `inverterPoints[0].last_refresh_time` |
| status ONLINE/OFFLINE | `inverterPoints[0].status == 1` |

### 2.7 Como re-testar rapidamente

```bash
TOKEN0='{"version":"v2.1.0","client":"ios","language":"en"}'
# login em qualquer host que responda (www, eu, au, hk); os dados vão para o host do campo "api"
LOGIN=$(curl -s https://eu.semsportal.com/api/v2/Common/CrossLogin -H "Token: $TOKEN0" \
  -H 'Content-Type: application/json' -d '{"account":"EMAIL","pwd":"SENHA"}')
TOKEN=$(echo "$LOGIN" | jq -c .data)
API=$(echo "$LOGIN" | jq -r .api)
for p in v3/PowerStation/GetPlantDetailByPowerstationId v3/PowerStation/GetInverterAllPoint; do
  curl -s "${API}$p" -H "Token: $TOKEN" -H 'Content-Type: application/json' \
    -d '{"powerStationId":"UUID"}' | jq -c '{code, keys: (.data | keys?)}'
done
curl -s "${API}v2/HistoryData/QueryPowerStationByHistory" -H "Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' | jq -c '{code, n: (.data.list | length)}'
```

Sinal de que o host caiu: `502` no login (foi assim com o `www` em 26/10). Sinal de host errado: `code 100002`
nas chamadas de dados logo depois de um login bem-sucedido.

Sinal de que morreu: `code 0` com `data` vazio/`null` para uma estação válida (foi assim em 23/09).

## 3. API web do SEMS+ (plano B)

**Testada com nossa conta em 2026-10-26** pelo cliente SEMS+ de referência do gw2pvo
(`gw2pvo/docs/sems_plus_api.py.reference`): o login no modo web funciona, com gateway
`https://us-gateway.semsportal.com/web/sems`, e o status, a energia do dia, a tensão e a localização vieram
certos. O login SEMS+ simples, sem cabeçalhos de navegador, devolve `code 100004` para esta conta. Fonte: código de `TimSoethout/goodwe-sems-home-assistant`
(`custom_components/sems/sems_api.py`, versões 11.x) e `timvanderHorst/goodwe-semsplus` (`api.py`).
As duas implementações são independentes e batem entre si.

### 3.1 Login

```
POST https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login
Headers:
  Content-Type: application/json
  Accept: application/json, text/plain, */*
  Origin: https://semsplus.goodwe.com
  Referer: https://semsplus.goodwe.com/
  Token: {"uid":"","timestamp":0,"token":"","client":"semsPlusWeb","version":"","language":"en"}
  X-Signature: <assinatura com uid/token vazios>
  User-Agent: <UA de navegador>   ← obrigatório desde 2026-09-23
Body:
  {"account":"<email>","pwd":"<base64(md5hex(senha))>","agreement":1,"isChinese":false,"isLocal":false}
```

- Sucesso: `code: "00000"`. `data` = token (uid, token, …) e `api` = gateway regional,
  ex.: `https://eu-gateway.semsportal.com/web/sems`. Se faltar, o fallback do HA é o gateway `eu`.
- Existe também um host regional de login (`https://eu-semsplus.goodwe.com/...`, visto em logs de usuários).
- Senha: `base64( md5(senha).hexdigest() )`, ou seja, o hex em minúsculas codificado em base64.

### 3.2 Assinatura (todas as chamadas com `client == "semsPlusWeb"`)

```
epoch_ms  = agora em milissegundos
digest    = sha256_hex( f"{epoch_ms}@{uid}@{token}" )
X-Signature = base64( f"{digest}@{epoch_ms}" )
```

Headers das chamadas autenticadas: `Content-Type/Accept: application/json`, `token: <json do token>`,
`X-Signature`, `User-Agent` de navegador.

### 3.3 Endpoints (base = gateway retornado no login)

| Método | Caminho | Uso |
|---|---|---|
| POST | `/sems-plant/api/portal/stations/page` body `{"current":1,"size":100}` | lista de estações → `data.dataList[].id` |
| GET | `/sems-plant/api/stations/device/all-status?stationId=<id>` | dispositivos → `deviceDetailList[].deviceType`, `statusDetailList[].{status, snList, detailMap}` |
| GET | `/sems-plant/api/equipments/<sn>/telemetry?deviceType=INVERTER&pwId=<id>` | tempo real (lista de grupos com `factors[] {code, data}`) |
| GET | `/sems-plant/api/equipments/<sn>/telecounting?deviceType=INVERTER&pwId=<id>` | contadores de energia |
| GET | `/sems-plant/api/stations/flow?stationId=<id>` | fluxo de potência (`pAc`/`pSystem` em kW) |
| POST | `/sems-plant/api/stations/statistics` body `{stationId, isReport:false, items[], dimension:"day"\|"year", startTime, endTime}` | séries históricas |
| POST | `/sems-plant/api/stations/production` (mesmo formato) | totais e `currency` |
| GET | `/sems-plant/api/equipments/<sn>/relatedDevices?...` | baterias/gabinetes |
| POST | `/sems-remote/api/v1/address/remote/...` | controle (bateria/inversor) |

Tipos de dispositivo vistos: `INVERTER`, `SMART_METER`, `ENERGY_STORAGE_INTEGRATED_CABINET`, `BATTERY_RACK`,
`DONGLE`, `BAT_SYS`.

**Códigos de campo (factors) relevantes para este binding:**

- telemetry:
  - `pAc`: potência em **kW** (o HA multiplica por 1000);
  - `Vac`/`PHASE-A:Vac`, `Iac`, `Fac`, `Temperature`, `hTotal`, `gridPF`;
  - `MPPT-n:Vpv`, `MPPT-n:Ipv`, `MPPT-n:Ppv`.
- telecounting:
  - `proPvStatsToday` (eday), `proPvStatsWeek`, `proPvStatsMonth` (mês), `proPvStatsYear`, `proPvStatsTotal` (etotal);
  - `ratedPower` (capacidade).
- statistics items: `proSystemTotalStats`, `proGridStats`, `proPurchaseStats`, `proConsumStats`,
  `proSelfConsumStats`, `proCharStats`, `proDischarStats`.

### 3.4 Problemas conhecidos (issues do HA, set/2026)

- `100004` "parameter error": requisição sem User-Agent de navegador (#217). A comunidade sugere variar o UA
  em vez de usar uma string fixa.
- `100025` "You do not have access or operation rights" na telemetria (#232).
- `C0602` "Unknown error" no `device/all-status` (#219, comentários).
- `GY0429`: rate limit (o HA espera 300 s).
- Algumas contas foram obrigadas a **trocar a senha** ("security level low") em 23/09 (#216).
- **Um novo login na API legada invalida a sessão web do SEMS+** no servidor (comentário no código do HA).
- À noite (status 0) não vem `pAc`, e `etotal` pode vir 0 entre timeouts, o que o HA registra como reset de medidor (#230).

### 3.5 Esboço de migração do binding (se os endpoints legados morrerem)

1. Login SEMS+ (3.1) com UA de navegador; guardar token + gateway.
2. `X-Signature` em toda chamada; re-login em sessão expirada.
3. Descoberta: `portal/stations/page`.
4. Status: `device/all-status` → para cada inversor, `telemetry` (`pAc`) + `telecounting`
   (`proPvStatsToday/Month/Total`); receita via `stations/production` (`profitProStats`, `currency`).
5. Tratar `pAc` ausente à noite e contadores zerados (não publicar 0 como total).
6. Validar com respostas reais e anonimizar os fixtures (como foi feito em 2026-09-24).

## 4. Repositórios de referência

| Repositório | O que é | Relevância |
|---|---|---|
| [TimSoethout/goodwe-sems-home-assistant](https://github.com/TimSoethout/goodwe-sems-home-assistant) | Integração HA mais usada (≈139★, desde 2019) | **Principal referência.** SEMS+ Web completo (ver abaixo) |
| [timvanderHorst/goodwe-semsplus](https://github.com/timvanderHorst/goodwe-semsplus) | Integração HA só SEMS+ ("pure requests") | Implementação menor e mais legível do login/assinatura (`custom_components/goodwe_semsplus/api.py`, `semsplus.py`) |
| [aroundmyroom/gw2pvo](https://github.com/aroundmyroom/gw2pvo) | Fork do gw2pvo para SEMS+ | Referência para o cliente Python gw2pvo |
| [markruys/gw2pvo](https://github.com/markruys/gw2pvo) | gw2pvo original | Origem do login com token `v3.1` + UA iOS + `api` regional |
| [ashwhall/ha-sems-plus-addon](https://github.com/ashwhall/ha-sems-plus-addon) | Add-on HA com scraping via Playwright | Obsoleto como referência: a API REST existe |
| [AaronSaikovski/gogoodwe](https://github.com/AaronSaikovski/gogoodwe) | CLI em Go para SEMS / SEMS Solar API | Não analisado |
| [bueste/ioBroker.goodwe-sems](https://github.com/bueste/ioBroker.goodwe-sems) | Adapter ioBroker | Não analisado |
| [hbeijeman/domoticz-goodwe](https://github.com/hbeijeman/domoticz-goodwe) | Domoticz, scraper SEMS+ | Não analisado |
| [intelseb/goodwesemsplus](https://github.com/intelseb/goodwesemsplus) | "Sems Plus Poller" | Não analisado |

**TimSoethout – pontos para acompanhar:**

- Arquivo: `custom_components/sems/sems_api.py` (≈1900 linhas); há também `api_examples/README.md`.
- PRs da migração (todos merged em 2026-09-23): #220, #221 (cliente SEMS+ Web), #222, #223, #225.
- Commits de 2026-09-25: `3851202` "Remove legacy monitor polling", `0dec286` "Use Web station discovery".
  A partir daí a leitura de dados e a descoberta são 100% SEMS+ Web. Do legado restam o login (fallback) e o
  `PowerStation/SaveRemoteControlInverter`.
- Releases: 11.0.0 (23/09, primeira com fallback web), 11.6.0 (estável em 25/09), 11.7–11.11 beta no mesmo dia.
- Issues: #216 (troca de senha), #217 (User-Agent), #219 (monitor vazio / fallback web), #230 (noite/etotal 0),
  #232 (100025 telemetria), #215 e #173 (múltiplos inversores).
- Eles **nunca usaram** `GetPlantDetailByPowerstationId`/`GetInverterAllPoint`: foram direto para a API web.

**Origem do binding:** contribuição inicial de Iwan Bron (maio/2021, openHAB PR #10100). Não cita o projeto do HA.
Usava os mesmos endpoints do HA na época (`v2/Common/CrossLogin` + `GetMonitorDetailByPowerstationId`), mas com
token diferente e com listagem própria. Provavelmente a mesma API conhecida da comunidade, não um port.

## 5. Pendências / ideias (fora do escopo da migração)

- [ ] Parse de `last_refresh_time` sem fallback (`DateTimeParseException` derrubaria a tarefa agendada).
- [ ] `monthTotal` atrasado um dia: avaliar trocar para `kpi.month_generation`.
- [ ] Múltiplos inversores: usar totais da planta (`kpi.power`, `kpi.total_power`) ou somar inversores.
- [ ] Testar a API web do SEMS+ com conta real (só leitura) e salvar respostas anonimizadas.
- [ ] Rodar `spotless:apply` antes de PR upstream (linhas longas pré-existentes quebram `spotless:check`).
- [ ] Reavaliar periodicamente: releases/issues do TimSoethout e o teste da seção 2.7.
