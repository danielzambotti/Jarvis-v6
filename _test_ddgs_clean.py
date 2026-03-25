import sys
sys.path.insert(0, 'C:\\Jarvis')
from skills.web_search import _ddg_search, _extract_query

q = _extract_query("qual o preco atual do Bitcoin em USD")
print("Query extracted:", q)
results = _ddg_search(q)
print("Results count:", len(results))
for r in results[:2]:
    title = r.get('title','')
    body = r.get('body','')[:150]
    print(f"  TITLE: {title}")
    print(f"  BODY:  {body}")
    print()
