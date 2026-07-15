# Painel de Vendas — Grupo Primum

Dashboard diário de **vendas** para a diretoria (Aristo · CBI · BWS · EDUQ/MedQ).
Réplica do painel-modelo de performance + camada de **Investimento, CPA e ROAS**.

## O que mostra
- KPIs: Total de Vendas, Receita, Pré-checkout (leads), Conversão, **Investimento, CPA, ROAS**
- Funil (pré-checkout → vendas → receita), evolução no tempo
- Anúncios por `utm_content` (Meta Ads / Search Ads)
- Volume por canal (Search / Meta / Redes Sociais / WhatsApp / Outros) com CPA e ROAS por canal
- Filtros: Hoje · Ontem · 7 dias · 30 dias · Mês atual · Mês anterior · Personalizado

## Fontes (Google Sheets, via service account)
- **Vendas Guru** — vendas + receita por marca (`utm_content` / `utm_source` / `canal`)
- **Grupo Primum | Leads e Pré-Checkout** — pré-checkouts / leads qualificados (BWS = médicos)
- **[PRIMUM][ALL_BUS][INVESTIMENTO_ADS]** — investimento Meta
- **[PRIMUM][ALL_BUS][INVESTIMENTO_GOOGLE]** — investimento Google

## Regra do investimento (CPA/ROAS)
Só campanhas de **venda** (token `vendas` no nome). Exclui captação, diagnóstico,
experience, distribuição e live dermatologia. Investimento disponível a partir de 01/07/2026.

## Atualização
`refresh.py` roda de hora em hora via GitHub Actions, relê as planilhas, regrava
`data.json` e injeta no `index.html` (dados embutidos, sem PII). Secret: `GOOGLE_SHEETS_CREDENTIALS_JSON`.

Rodar local: `python refresh.py` (usa `~/.claude/skills/google-sheets/.env`).
