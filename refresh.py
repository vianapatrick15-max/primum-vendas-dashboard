#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline do Painel de Vendas Primum (Aristo / CBI / BWS / EDUQ-MedQ).
Cruza 4 planilhas -> data.json granular (dia x marca x anuncio x canal + investimento venda Meta+Google).
Regra do investimento: SO campanhas com token 'vendas' (exclui captacao/distribuicao).
"""
import warnings; warnings.filterwarnings('ignore')
import os, sys, re, json, datetime
from pathlib import Path
sys.path = [p for p in sys.path if p not in ('', '/tmp', '/private/tmp')]
import gspread
from google.oauth2.service_account import Credentials
from collections import defaultdict

BASE = Path(__file__).parent
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

def get_client():
    """CI: creds no env GOOGLE_SHEETS_CREDENTIALS_JSON (conteudo). Local: path no .env."""
    raw = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_JSON')
    if raw:
        return gspread.authorize(Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES))
    envf = Path.home() / '.claude/skills/google-sheets/.env'
    path = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_PATH')
    if not path and envf.exists():
        path = re.search(r'GOOGLE_SHEETS_CREDENTIALS_PATH="?([^"\n]+)', envf.read_text()).group(1).strip()
    return gspread.authorize(Credentials.from_service_account_file(path, scopes=SCOPES))

GC = get_client()

SID_VENDAS   = '10jB14tRlVdN1R4EqWjC6p8PMsU1WHJd1rb0r56AfUgo'
SID_PRECHECK = '1vcpyCCE0d8zvoSfZqEacvRcJ3yQQgZdogO7CJu32MwA'
SID_META     = '1XUl1Mncrq2-DiWxwduUdHRW0_YuI9fwUml3lRs_-XNU'
SID_GOOGLE   = '1BEk0r4k8BLzr8gqtJbBcXZgFzxti94NgwjrKixBuVsI'

BRANDS = {
    'aristo': {'label': 'Aristo', 'vendas': 'Aristo', 'precheck': '[ART] Pré-checkouts Hubspot', 'meta': 'ARISTO', 'google': 'Aristo'},
    'cbi':    {'label': 'CBI',    'vendas': 'CBI',    'precheck': '[CBI] Pré-checkouts Hubspot', 'meta': 'CBI',   'google': 'CBI Of Miami'},
    'bws':    {'label': 'BWS',    'vendas': 'BWS',    'precheck': '[BWS] Leads Hubspot',          'meta': 'BWS',   'google': 'BWS Pós Médica', 'qual': 'é médico?', 'inv_tokens': ('vendas', 'captacao')},
    'eduq':   {'label': 'EDUQ',   'vendas': 'MedQ',   'precheck': '[MDQ] Pré-checkouts Hubspot', 'meta': 'MDQ',   'google': 'EduQ | Medq'},
}

def brl(v):
    """Parse numero pt-BR ('4993,96', '1.884,00', '380,27') -> float."""
    if v is None: return 0.0
    s = str(v).strip()
    if not s: return 0.0
    s = re.sub(r'[^0-9,.\-]', '', s)
    if not s or s in ('-', '.', ','): return 0.0
    if ',' in s:                      # virgula = decimal, ponto = milhar
        s = s.replace('.', '').replace(',', '.')
    # se so tem ponto, assume decimal (Google/Meta ja vem assim as vezes)
    try: return float(s)
    except: return 0.0

def norm_date(s):
    """Qualquer formato -> 'YYYY-MM-DD' (ou None)."""
    if not s: return None
    s = str(s).strip()
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m: return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', s)   # DD/MM/YYYY
    if m: return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    return None

def channel_bucket(src):
    """Bucket do VOLUME POR CANAL. Aceita utm_source (search_ads/meta_ads/social...) e
    o canal amigavel da BWS (Search/Meta/Instagram/Whatsapp/Organico/Importacao...)."""
    s = (src or '').strip().lower()
    if 'search' in s or 'pmax' in s: return 'search'          # Google Ads = search + pmax
    if 'whats' in s or s == 'wpp': return 'whatsapp'
    if s in ('social', 'ig', 'instagram') or 'instagram' in s: return 'social'
    if s == 'meta_ads' or s == 'meta' or s.startswith('meta') or 'facebook' in s or s == 'fb': return 'meta'
    return 'outros'   # vazio, exec, direct, organico, importacao, sem rastreio...

def ad_network(src):
    """Rede das tabelas de ANUNCIO: search (google) ou meta (so meta_ads pago) ou None.
    'social'/'instagram' NAO entram em anuncios (vao so pro canal Redes Sociais)."""
    s = (src or '').strip().lower()
    if 'search' in s or 'pmax' in s: return 'search'
    if s in ('meta_ads', 'meta') or 'facebook' in s or s == 'fb': return 'meta'
    return None

# tokens que NUNCA contam como investimento (branding/lead-magnet de outro produto)
NAO_INV = ('diagnostico', 'diagnóstico', 'experience', 'distribu', 'live')
def is_inv_camp(camp, tokens):
    """Conta como investimento de aquisicao se bate algum `tokens` e nao esta em NAO_INV.
    Padrao tokens=('vendas',) (MedQ/CBI/Aristo). BWS = ('vendas','captacao') pois a
    matricula vem do funil de captacao (faculdade), nao de campanha de venda direta."""
    c = (camp or '').lower()
    if any(x in c for x in NAO_INV): return False
    return any(t in c for t in tokens)

def header_map(rows):
    hdr = rows[0]
    return {h.strip().lower(): i for i, h in enumerate(hdr) if h.strip()}

def col(hm, *names):
    for n in names:
        if n.lower() in hm: return hm[n.lower()]
    return None

def get(row, idx):
    if idx is None or idx >= len(row): return ''
    return row[idx]

# ---------------- coleta ----------------
def load(sid, tab):
    return GC.open_by_key(sid).worksheet(tab).get_values()

data = {}
for key, cfg in BRANDS.items():
    daily   = defaultdict(lambda: {'precheck': 0, 'vendas': 0, 'receita': 0.0, 'inv_meta': 0.0, 'inv_google': 0.0})
    ads     = defaultdict(lambda: {'vendas': 0, 'precheck': 0, 'receita': 0.0})                   # key=(date,network,content)
    chan    = defaultdict(lambda: {'vendas': 0, 'receita': 0.0})                                  # key=(date,bucket)

    # VENDAS  (BWS nao tem UTM: usa a coluna 'canal' como fonte, e nao tem utm_content)
    v = load(SID_VENDAS, cfg['vendas']); hm = header_map(v)
    c_date = col(hm, 'data_venda'); c_content = col(hm, 'utm_content'); c_src = col(hm, 'utm_source', 'canal'); c_rec = col(hm, 'receita_contratada')
    for r in v[1:]:
        d = norm_date(get(r, c_date))
        if not d: continue
        rec = brl(get(r, c_rec)); src = get(r, c_src); content = (get(r, c_content) or '').strip()
        b = channel_bucket(src); net = ad_network(src)
        daily[d]['vendas'] += 1; daily[d]['receita'] += rec
        chan[(d, b)]['vendas'] += 1; chan[(d, b)]['receita'] += rec
        if net and content:
            k = (d, net, content); ads[k]['vendas'] += 1; ads[k]['receita'] += rec

    # PRE-CHECKOUT  (BWS: so leads QUALIFICADOS = medico, via coluna 'É Médico?')
    try:
        p = load(SID_PRECHECK, cfg['precheck']); hm = header_map(p)
        c_date = col(hm, 'data de conversão recente', 'data de conversao recente', 'data de conversão');
        c_content = col(hm, 'utm content'); c_src = col(hm, 'utm source')
        c_qual = col(hm, cfg['qual']) if cfg.get('qual') else None
        for r in p[1:]:
            d = norm_date(get(r, c_date))
            if not d: continue
            if c_qual is not None and not str(get(r, c_qual)).strip().lower().startswith('sim'):
                continue   # BWS: ignora nao-qualificado
            daily[d]['precheck'] += 1
            src = get(r, c_src); content = (get(r, c_content) or '').strip(); net = ad_network(src)
            if net and content:
                ads[(d, net, content)]['precheck'] += 1
    except Exception as e:
        print('WARN precheck', key, cfg['precheck'], e, file=sys.stderr)

    # INVESTIMENTO META (so 'vendas')
    m = load(SID_META, cfg['meta']); hm = header_map(m)
    c_day = col(hm, 'day'); c_camp = col(hm, 'campaign name'); c_spend = col(hm, 'amount spent')
    inv_tokens = cfg.get('inv_tokens', ('vendas',))
    for r in m[1:]:
        d = norm_date(get(r, c_day))
        if not d or not is_inv_camp(get(r, c_camp), inv_tokens): continue
        daily[d]['inv_meta'] += brl(get(r, c_spend))

    # INVESTIMENTO GOOGLE (so 'vendas', filtrando pela conta da marca)
    g = load(SID_GOOGLE, 'Página1'); hm = header_map(g)
    c_day = col(hm, 'day'); c_acct = col(hm, 'account name'); c_camp = col(hm, 'campaign name'); c_cost = col(hm, 'cost (spend)', 'cost')
    for r in g[1:]:
        d = norm_date(get(r, c_day)); acct = (get(r, c_acct) or '').strip()
        if not d or acct != cfg['google'] or not is_inv_camp(get(r, c_camp), inv_tokens): continue
        daily[d]['inv_google'] += brl(get(r, c_cost))

    data[key] = {
        'label': cfg['label'],
        'daily': {d: {k: round(x, 2) if isinstance(x, float) else x for k, x in v.items()} for d, v in sorted(daily.items())},
        'ads': [{'date': k[0], 'network': k[1], 'content': k[2], 'vendas': v['vendas'], 'precheck': v['precheck'], 'receita': round(v['receita'], 2)}
                for k, v in ads.items()],
        'channels': [{'date': k[0], 'bucket': k[1], 'vendas': v['vendas'], 'receita': round(v['receita'], 2)}
                     for k, v in chan.items()],
    }

try:
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo('America/Sao_Paulo'))
except Exception:
    now = datetime.datetime.utcnow() - datetime.timedelta(hours=3)
out = {'generated_at': now.strftime('%d/%m/%Y %H:%M'), 'brands': data}

payload = json.dumps(out, ensure_ascii=False, separators=(',', ':'))
(BASE / 'data.json').write_text(payload)
# embute os dados no template -> index.html (sem fetch, robusto igual os outros dashboards)
tpl = (BASE / 'template.html').read_text()
(BASE / 'index.html').write_text(tpl.replace('__DATA__', payload))

# ---------------- validacao rapida ----------------
def period_sum(key, ini, fim):
    dd = data[key]['daily']; s = {'precheck': 0, 'vendas': 0, 'receita': 0.0, 'inv_meta': 0.0, 'inv_google': 0.0}
    for d, v in dd.items():
        if ini <= d <= fim:
            for k in s: s[k] += v[k]
    return s

print('gerado:', out['generated_at'])
for key in BRANDS:
    s = period_sum(key, '2026-07-01', '2026-07-31')
    inv = s['inv_meta'] + s['inv_google']
    cpa = inv / s['vendas'] if s['vendas'] else 0
    roas = s['receita'] / inv if inv else 0
    print(f"{key:6} JUL | precheck {s['precheck']:4} | vendas {s['vendas']:4} | receita R$ {s['receita']:11,.0f} | "
          f"inv(M/G) {s['inv_meta']:8,.0f}/{s['inv_google']:8,.0f} | CPA {cpa:8,.0f} | ROAS {roas:5.1f}")
# canais CBI julho (validar print)
cb = defaultdict(lambda: [0, 0.0])
for c in data['cbi']['channels']:
    if '2026-07' in c['date']:
        cb[c['bucket']][0] += c['vendas']; cb[c['bucket']][1] += c['receita']
print('CBI canais JUL:', {k: (n, round(r)) for k, (n, r) in cb.items()})
