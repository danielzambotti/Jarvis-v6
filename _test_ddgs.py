from duckduckgo_search import DDGS
print("DDGS import: OK")
with DDGS() as ddgs:
    r = [x for x in ddgs.text("bitcoin price USD", max_results=2)]
print(f"Search results: {len(r)}")
if r:
    print("First result title:", r[0].get("title"))
    print("First result body:", r[0].get("body","")[:120])
