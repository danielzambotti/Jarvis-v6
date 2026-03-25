from ddgs import DDGS
print("ddgs import: OK")
d = DDGS()
r = list(d.text("bitcoin price USD", max_results=2))
print("results:", len(r))
if r:
    print("title:", r[0].get("title"))
    print("body:", r[0].get("body","")[:100])
else:
    print("EMPTY RESULTS")
