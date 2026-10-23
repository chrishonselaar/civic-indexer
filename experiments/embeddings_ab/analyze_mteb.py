import csv

SUM='./MTEB_nld_v1_summary.csv'
PER='./MTEB_nld_v1_per_task.csv'

def load(p):
    with open(p) as f:
        return {r['Model']: r for r in csv.DictReader(f)}

s=load(SUM); p=load(PER)

def fnum(v):
    try: return float(v)
    except: return None

# civic-relevant retrieval tasks (our workload: gov/legal/news/tender doc search)
CIVIC=['bBSARDNLRetrieval','LegalQANLRetrieval','OpenTenderRetrieval','DutchNewsArticlesRetrieval']

rows=[]
for m,sr in s.items():
    pr=p.get(m,{})
    tp=fnum(sr.get('Total Params (B)'))
    retr=fnum(sr.get('Retrieval'))
    civ=[fnum(pr.get(t)) for t in CIVIC]
    civ=[x for x in civ if x is not None]
    civm=sum(civ)/len(civ) if civ else None
    rows.append(dict(model=m, tp=tp, dim=sr.get('Embedding Dim'), maxtok=sr.get('Max Tokens'),
                     retr=retr, civ=civm,
                     legal=fnum(pr.get('bBSARDNLRetrieval')), legalqa=fnum(pr.get('LegalQANLRetrieval')),
                     tender=fnum(pr.get('OpenTenderRetrieval')), news=fnum(pr.get('DutchNewsArticlesRetrieval'))))

def shortname(m): return m if len(m)<=40 else m[:39]+'…'

print("="*118)
print("TOP 15 by RETRIEVAL aggregate (all sizes)")
print(f"{'model':40} {'TotB':>6} {'maxtok':>6} {'Retr':>6} {'Civic':>6} {'bBSARD':>6} {'LegalQA':>7} {'Tender':>6} {'News':>6}")
for r in sorted([r for r in rows if r['retr'] is not None], key=lambda x:-x['retr'])[:15]:
    print(f"{shortname(r['model']):40} {r['tp'] if r['tp'] else '?':>6} {str(r['maxtok']):>6} "
          f"{r['retr']:>6.2f} {r['civ'] if r['civ'] else 0:>6.2f} "
          f"{r['legal'] or 0:>6.2f} {r['legalqa'] or 0:>7.2f} {r['tender'] or 0:>6.2f} {r['news'] or 0:>6.2f}")

print("="*118)
print("CHEAP-TIER LEADERS: Total Params <= 0.65B, ranked by RETRIEVAL aggregate")
print(f"{'model':40} {'TotB':>6} {'maxtok':>6} {'Retr':>6} {'Civic':>6} {'bBSARD':>6} {'LegalQA':>7} {'Tender':>6} {'News':>6}")
cheap=[r for r in rows if r['tp'] is not None and r['tp']<=0.65 and r['retr'] is not None]
for r in sorted(cheap, key=lambda x:-x['retr'])[:12]:
    print(f"{shortname(r['model']):40} {r['tp']:>6} {str(r['maxtok']):>6} "
          f"{r['retr']:>6.2f} {r['civ'] if r['civ'] else 0:>6.2f} "
          f"{r['legal'] or 0:>6.2f} {r['legalqa'] or 0:>7.2f} {r['tender'] or 0:>6.2f} {r['news'] or 0:>6.2f}")

print("="*118)
print("CIVIC-RETRIEVAL leaders (mean of legal+tender+news retrieval), any size, top 12")
print(f"{'model':40} {'TotB':>6} {'Civic':>6} {'bBSARD':>6} {'LegalQA':>7} {'Tender':>6} {'News':>6} {'Retr':>6}")
for r in sorted([r for r in rows if r['civ'] is not None], key=lambda x:-x['civ'])[:12]:
    print(f"{shortname(r['model']):40} {r['tp'] if r['tp'] else '?':>6} {r['civ']:>6.2f} "
          f"{r['legal'] or 0:>6.2f} {r['legalqa'] or 0:>7.2f} {r['tender'] or 0:>6.2f} {r['news'] or 0:>6.2f} {r['retr'] or 0:>6.2f}")

print("="*118)
print("Reference points (design baselines + Qwen3 ladder), wherever they rank")
for needle in ['bge-m3','Qwen3-Embedding-0.6B','Qwen3-Embedding-4B','Qwen3-Embedding-8B','Octen-Embedding-8B','Octen-Embedding-4B','multilingual-e5','Euler-Legal','granite-embedding-278m','Solon']:
    for r in rows:
        if needle.lower() in r['model'].lower():
            print(f"  {shortname(r['model']):42} TotB={r['tp']} maxtok={r['maxtok']} Retr={r['retr']} Civic={round(r['civ'],2) if r['civ'] else None} legal={r['legal']} legalQA={r['legalqa']} tender={r['tender']}")
