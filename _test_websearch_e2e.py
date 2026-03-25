import sys
sys.path.insert(0, 'C:\\Jarvis')
from skills.web_search import execute

print("=== TEST 1: Bitcoin price ===")
result = execute("Qual o preco atual do Bitcoin em USD?")
print(result[:500])
print()
print("=== TEST 2: Tech news ===")
result2 = execute("Me de um resumo das 3 noticias de tecnologia mais importantes das ultimas 24 horas")
print(result2[:500])
