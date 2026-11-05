# agent_seo.py — L'agent SEO qui analyse une page et recommande
import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from analyseur import analyser_page   # on réutilise ton outil !

load_dotenv()

# 1. Le cerveau
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT"),
    api_version="2024-10-21",
)

# 2. On transforme ton analyseur en OUTIL pour l'agent
@tool
def audit_seo(url: str) -> dict:
    """Analyse les éléments SEO d'une page web à partir de son URL."""
    return analyser_page(url)

outils = [audit_seo]

# 3. Les instructions (ici on met ton expertise SEO)
prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es un expert SEO. Quand on te donne une URL, utilise l'outil audit_seo "
     "pour récupérer les données, puis produis un rapport clair avec : "
     "1) Les points forts, 2) Les problèmes détectés, 3) Des recommandations "
     "concrètes et priorisées. Base-toi UNIQUEMENT sur les données de l'outil."),
    ("human", "{input}"),
    ("placeholder", "{agent_scratchpad}"),
])

# 4. Assemblage
agent = create_tool_calling_agent(llm, outils, prompt)
executeur = AgentExecutor(agent=agent, tools=outils, verbose=True)

# 5. On lance !
url = "https://explorawatersports.com"
print(f"\n🔍 Audit SEO de : {url}\n")
reponse = executeur.invoke({"input": f"Fais un audit SEO complet de {url}"})

print("\n📊 RAPPORT SEO :")
print(reponse["output"])