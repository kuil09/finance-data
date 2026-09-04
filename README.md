# finance-data

`finance-data`는 세계 주요 경제·금융 관련 공개 데이터를 신뢰 가능한 원천에서 수집하고, 서로 다른 API·스키마·단위·시간 표현을 일관된 데이터셋 형태로 정규화하여 저장하는 프로젝트다.

목표는 시장을 분석하거나 투자 결론을 만드는 것이 아니다.

목표는 **다른 프로그램과 AI 에이전트가 경제 데이터를 직접 원천 API마다 해석하지 않아도, 동일한 방식으로 발견하고 이해하고 사용할 수 있는 데이터 기반을 만드는 것**이다.

---

## Why

경제 데이터는 이미 많이 공개되어 있다.

문제는 데이터가 없다는 것이 아니라 서로 다른 방식으로 존재한다는 것이다.

미국 재무부, EIA, BLS, FRED, ECB, BIS, IMF 같은 기관은 각자 신뢰도 높은 데이터를 제공하지만 다음이 모두 다르다.

- API 규격
- 데이터 구조
- 필드 이름
- 단위
- 날짜와 시간 표현
- 갱신 주기
- 지역 코드
- 상품 및 지표 식별자
- 수정 및 결측값 표현
- 메타데이터 수준

따라서 데이터를 사용하는 쪽에서는 같은 작업을 반복한다.

```text
API 조사
→ 인증 및 요청 구현
→ 응답 구조 분석
→ 필드 의미 확인
→ 날짜 변환
→ 단위 확인
→ 데이터 정제
→ 출처 기록
→ 자체 저장
```

`finance-data`는 이 작업을 공통 기반으로 분리한다.

```text
Official / Reliable Public Sources
                │
                ▼
              Collect
                │
                ▼
          Preserve Raw Data
                │
                ▼
             Normalize
                │
                ▼
             Validate
                │
                ▼
          Dataset Storage
                │
          ┌─────┴─────┐
          ▼           ▼
       Catalog       Query
          │           │
          └─────┬─────┘
                ▼
        Programs / Agents
```

---

## Project Goal

세계 주요 경제 데이터를 다음 조건을 만족하는 형태로 축적한다.

### Reliable

가능하면 정부기관, 중앙은행, 국제기구 등 1차 원천을 사용한다.

```text
Primary official source
        >
Official aggregator
        >
Reliable public secondary source
```

데이터와 함께 반드시 원천 정보도 보존한다.

### Reproducible

수집한 원본 데이터는 가능한 한 그대로 보존한다.

정규화 코드가 변경되더라도 원본으로부터 데이터를 다시 생성할 수 있어야 한다.

```text
RAW
 ↓
NORMALIZED
```

두 계층을 명확하게 분리한다.

### Consistent

서로 다른 API에서 제공되는 데이터를 공통 규칙으로 표현한다.

예를 들어 다음과 같은 차이를 정리한다.

```text
2026/09/05
2026-09-05
Sep 5 2026

→ 2026-09-05
```

또한 숫자, null, timezone, 단위, 지역 코드 등의 표현을 가능한 범위에서 일관되게 만든다.

### Discoverable

에이전트나 프로그램이 데이터셋 이름을 미리 알고 있어야만 사용할 수 있는 시스템을 만들지 않는다.

각 데이터셋은 설명 가능한 metadata를 가진다.

예:

```yaml
id: commodity.oil.wti.spot

title: WTI Crude Oil Spot Price

classification:
  domain: commodity
  topic: crude_oil

temporal:
  frequency: daily

value:
  unit: USD_per_barrel

source:
  organization: U.S. Energy Information Administration
```

사용자는 데이터 자체뿐 아니라 다음도 확인할 수 있어야 한다.

- 무엇을 측정한 데이터인가
- 어느 지역의 데이터인가
- 단위는 무엇인가
- 갱신 주기는 무엇인가
- 데이터 기간은 어디까지인가
- 어떤 기관에서 제공하는가
- 어떤 원본 필드에서 왔는가

### Agent-friendly

AI 에이전트가 기관별 API 문서와 필드명을 매번 새로 해석하지 않아도 데이터를 다룰 수 있어야 한다.

예를 들어 에이전트는 다음과 같은 공통 인터페이스를 사용할 수 있다.

```bash
finance datasets
```

```bash
finance datasets search oil
```

```bash
finance describe commodity.oil.wti.spot
```

```bash
finance query commodity.oil.wti.spot \
  --from 2026-01-01 \
  --to 2026-09-01
```

원천이 EIA인지 Treasury인지 FRED인지에 따라 소비자의 사용 방법이 달라지지 않는 것을 목표로 한다.

---

## Core Concept: Dataset

`finance-data`의 중심 개념은 API나 기관이 아니라 **Dataset**이다.

예:

```text
us.fiscal.debt.total
commodity.oil.wti.spot
commodity.oil.brent.spot
us.energy.petroleum.inventory
us.energy.electricity.demand
us.macro.cpi
us.macro.unemployment
```

Dataset은 경제적 의미를 표현한다.

반면 Source는 그 데이터를 어디서 가져오는지를 표현한다.

```text
Dataset
    │
    ├── Source
    ├── Schema
    ├── Dimensions
    ├── Measures
    └── Temporal Metadata
```

이 둘을 분리하는 것이 중요한 설계 원칙이다.

```text
dataset identity ≠ source identity
```

같은 경제 데이터가 여러 기관에서 제공될 수도 있고, 하나의 기관이 수백 개의 데이터셋을 제공할 수도 있기 때문이다.

---

## Dataset Model

모든 경제 데이터를 단순한 `date → value` 시계열로 변환하지 않는다.

다양한 데이터를 표현할 수 있도록 기본 모델을 다음처럼 정의한다.

```text
Dataset
    │
    ├── Period
    ├── Dimensions
    └── Measures
```

예를 들어 WTI 가격은 단순하다.

```json
{
  "dataset": "commodity.oil.wti.spot",
  "period": "2026-09-01",
  "dimensions": {},
  "measures": {
    "price": 64.31
  }
}
```

원유 재고는 dimension을 가질 수 있다.

```json
{
  "dataset": "us.energy.petroleum.inventory",
  "period": "2026-09-01",
  "dimensions": {
    "region": "US",
    "product": "crude_oil"
  },
  "measures": {
    "stock": 418300
  }
}
```

전력 데이터는 시간 및 지역 dimension을 더 가질 수 있다.

```json
{
  "dataset": "us.energy.electricity.demand",
  "period": "2026-09-01T13:00:00-05:00",
  "dimensions": {
    "balancing_authority": "ERCOT"
  },
  "measures": {
    "demand": 72481
  }
}
```

Dataset마다 자체 schema를 가질 수 있으며, 공통 계약은 dataset discovery와 metadata, provenance, temporal information을 중심으로 유지한다.

---

## Source

Source는 데이터를 제공하는 외부 시스템을 의미한다.

초기 대상은 다음과 같은 공개 데이터 제공자를 고려한다.

```text
U.S. Treasury
EIA
BLS
BEA
FRED / ALFRED
Federal Reserve
ECB
BIS
IMF
OECD
Bank of Korea
KOSIS
```

Source 정의에는 최소한 다음 정보가 포함된다.

```yaml
id: us_eia

organization:
  name: U.S. Energy Information Administration

authority:
  level: primary

access:
  type: api

quality:
  official: true
```

Source는 데이터 자체와 분리하여 관리한다.

---

## Adapter

Adapter는 외부 API와 `finance-data` 사이의 기술적 연결을 담당한다.

예:

```text
Treasury Fiscal Data Adapter
EIA v2 Adapter
FRED Adapter
SDMX Adapter
CSV Adapter
```

하나의 Adapter가 여러 Dataset을 처리할 수 있다.

특히 동일한 protocol이나 API 구조를 공유하는 데이터는 가능한 범위에서 adapter를 재사용한다.

---

## Raw Data

외부 시스템에서 받은 데이터는 가능한 한 원본 그대로 보존한다.

Raw 계층에서는 의미를 변경하지 않는다.

```text
raw/
  source=eia/
  ...

raw/
  source=us_treasury/
  ...
```

Raw 데이터와 함께 다음 provenance 정보를 기록한다.

```text
source
request
retrieved_at
content_hash
response metadata
```

Raw 데이터는 정규화 과정의 오류나 schema 변경이 발생했을 때 다시 데이터를 구축할 수 있는 기반이 된다.

---

## Normalization

Normalization은 데이터를 분석하는 작업이 아니다.

허용되는 작업은 데이터의 **표현을 정리하는 것**이다.

예:

```text
"1,293.40"
→ 1293.40
```

```text
"."
→ null
```

```text
2026/09/01
→ 2026-09-01
```

```text
EST
→ timezone metadata
```

```text
source-specific region code
→ canonical region identifier
```

원천에서 표현하지 않은 경제적 의미를 새로 만들어내지 않는다.

---

## What This Project Does Not Do

이 프로젝트의 책임 범위를 명확하게 제한한다.

`finance-data`는 다음을 수행하지 않는다.

### Investment analysis

```text
BUY
SELL
시장 전망
포트폴리오 판단
```

### Economic interpretation

```text
경기가 침체 상태다
유동성이 악화되었다
인플레이션 압력이 증가했다
```

### State assembly

여러 데이터셋을 조합해 하나의 경제 상태를 정의하지 않는다.

### Derived indicators

예:

```text
Oil Stress Index
Liquidity Score
Fiscal Risk Score
Recession Probability
```

등의 합성 지표를 생성하지 않는다.

### Causal inference

```text
유가 상승
→ CPI 상승
→ 금리 상승
```

같은 인과관계를 판단하지 않는다.

### Strategy generation

경제 데이터를 투자 전략이나 행동으로 변환하지 않는다.

이러한 작업은 `finance-data`를 사용하는 상위 시스템의 책임이다.

---

## Data Principles

### Preserve source meaning

정규화 과정에서 원천 데이터의 의미를 바꾸지 않는다.

### Preserve original frequency

모든 데이터를 daily 데이터로 변환하지 않는다.

```text
Treasury debt        daily
Electricity          hourly
CPI                  monthly
GDP                  quarterly
Treasury auction     event
```

각 데이터가 가진 원래 시간적 특성을 유지한다.

### Preserve provenance

모든 데이터는 원천까지 추적 가능해야 한다.

```text
normalized record
       ↓
dataset
       ↓
source field
       ↓
raw response
       ↓
official source
```

### Prefer explicit metadata over inference

에이전트가 컬럼명이나 숫자를 보고 의미를 추측하게 하지 않는다.

가능한 정보는 Dataset metadata에 명시한다.

### Normalize representation, not meaning

다음을 목표로 한다.

```text
same meaning
different representation

→ common representation
```

다음을 목표로 하지 않는다.

```text
multiple facts

→ new interpretation
```

---

## Repository Structure

초기 구조는 다음을 기준으로 한다.

```text
finance-data/
│
├── datasets/
│   ├── us/
│   └── global/
│
├── sources/
│
├── adapters/
│
├── schemas/
│
├── collectors/
│
├── normalizers/
│
├── validators/
│
├── catalog/
│
├── storage/
│
├── cli/
│
└── tests/
```

### `datasets/`

경제적 데이터셋 정의.

### `sources/`

외부 데이터 제공기관 및 API 정의.

### `adapters/`

외부 API protocol 및 응답 처리.

### `collectors/`

수집 실행 로직.

### `normalizers/`

원본 데이터를 dataset schema로 변환.

### `validators/`

schema, type, completeness 등 기계적으로 검증 가능한 조건을 확인.

### `catalog/`

전체 dataset discovery index.

### `storage/`

Raw 및 normalized storage interface.

---

## Example Datasets

초기에는 서로 성격이 다른 데이터셋을 선택하여 구조의 범용성을 검증한다.

```text
US Treasury
└── Total Public Debt Outstanding

EIA
├── WTI Crude Oil Price
├── Petroleum Inventory
└── Electricity Demand

BLS
└── Consumer Price Index

Federal Reserve / FRED
└── Interest Rates
```

특정 영역을 빠르게 확장하는 것보다 서로 다른 구조·빈도·dimension을 가진 데이터를 동일한 Dataset 계약 아래에서 처리할 수 있는지를 먼저 검증한다.

---

## Long-term Direction

`finance-data` 자체는 경제를 해석하지 않는다.

대신 다양한 상위 시스템이 공통 데이터 기반을 공유할 수 있게 한다.

```text
                   finance-data
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   Research Agent   Macro Agent    Visualization
        │               │
        ▼               ▼
 Market Analysis    State Model
        │
        ▼
 Investment Agent
```

하위 데이터 계층을 가능한 한 단순하고 신뢰할 수 있게 유지함으로써 상위 시스템이 서로 다른 분석 방법을 자유롭게 구현할 수 있도록 한다.

---

## Definition

> **finance-data is a normalized distribution layer for reliable public economic data.**

세계 주요 공식·공개 경제 데이터의 서로 다른 API, schema, 단위, 주기와 식별 체계를 원천 의미를 훼손하지 않는 공통 Dataset 계약으로 정리하고 보존하여, 사람과 프로그램과 AI 에이전트가 동일한 방식으로 재사용할 수 있게 만드는 것이 이 프로젝트의 목적이다.
